import argparse
import asyncio
import logging
import math
import os
import random
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Literal, Optional, Set, Tuple, Union

import instructor
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from nutrition_engine import (
    ADAPTIVE_TDEE_TOLERANCE,
    DEFAULT_FIBER_FLOOR_G,
    DEFAULT_NET_CARBS_G,
    calculate_adaptive_tdee,
    calculate_fiber_target_g,
    calculate_macro_targets,
)
from repository import (
    PROJECT_ROOT,
    LocalJSONRepository,
    PlanRepository,
    StoragePaths,
    run_sync,
)
from shopping import (
    ShoppingList,
    aggregate_cook_events,
    apply_pantry,
    categorize_department,
    collect_unique_plants,
    ingredient_draws_on,
    format_shopping_list_markdown,
    format_shopping_list_text,
    round_ingredient_quantity,
)
from week import (
    DEFAULT_INVENTORY_RULES,
    DEFAULT_STORAGE_WINDOWS,
    MACRO_KEYS,
    STORAGE_CLASSES,
    STORAGE_CLASS_DEFAULT,
    DEFAULT_MEAL_TYPES,
    DEFAULT_SERVINGS_PER_MEAL,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    ShoppingWindow,
    SlotSpec,
    WeekSpec,
    cook_day_index,
    day_date,
    default_week_spec,
    eaten_on,
    fridge_day_gaps,
    humanize,
    location_for,
    location_rule,
    meal_types,
    parse_slot_id,
    pin_recipe,
    pin_style,
    portions_for,
    shopping_windows,
    skip_estimate_totals,
    slot_id,
    prep_day_batch_slot_ids,  # re-exported: see its docstring for why it moved
    slot_label,
    span_days,
    storage_safety_errors,
    storage_class_label,
    styles_for,
    validate_week,
    week_date_range,
)

# Explicit path rather than a bare `load_dotenv()`. The no-arg form searches
# upward from the *calling* file, which still finds the root `.env` from
# `src/` — but only by walking one directory it isn't told about. Naming the
# file means the CLI, the UI and a future entry point in any subdirectory all
# read the same secrets file.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DEFAULT_ALLOWED_NOVA_GROUPS = [1, 2, 3]

# Where the local files live is repository.py's business now; this default
# instance exists only so the CLI's --help text and pre-repository log
# messages have a filename to print before a LocalJSONRepository is
# constructed. Once a repository exists, read its own `.paths` instead.
DEFAULT_STORAGE_PATHS = StoragePaths()
# In `logs/`, not the repo root: it is disposable runtime output, and
# anchoring it means the log lands in the same place whether the CLI was
# started from the root or from `src/`.
LOG_FILE = DEFAULT_STORAGE_PATHS.generation_log

# OpenRouter's endpoint. A constant rather than config: it is the same URL for
# every model in `models.json`, and a per-install override was a knob nothing
# ever turned that `_require_models_config` still had to police on every call.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

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
    # `logs/` is gitignored wholesale, so a fresh clone has the directory only
    # because of its .gitkeep — and nothing stops someone deleting it. Creating
    # it here means a missing log directory can never be what stops a run.
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

def short_error(exc: Exception) -> str:
    """An exception as one loggable/notifiable line.

    Every failure path in this app has to survive being put in a toast, a
    `WeekPlan.failures` value and a log line, and instructor's validation
    errors are multi-paragraph — first line only, truncated. One helper so all
    eight call sites truncate identically; they had each spelled this out.
    """
    return f"{type(exc).__name__}: {exc}".split("\n")[0][:300]


def log_completion(label: str, completion, started: float) -> None:
    """Record one model call's timing and token usage to `meals.log`.

    `reasoning_tokens` is the load-bearing field, not `elapsed`: a reasoning
    blowup and a merely slow provider both look like a long wait, and only the
    token counts tell them apart (see "Reasoning must be disabled"). Pulled out
    of the four call sites that each rebuilt this `getattr` chain — a
    diagnostic that reports different fields depending on which call produced
    it is worse than no diagnostic.
    """
    usage = getattr(completion, "usage", None)
    logger.info(
        "%s: got response in %.1fs (finish_reason=%s, completion_tokens=%s, reasoning_tokens=%s)",
        label,
        time.monotonic() - started,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
        getattr(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
        ),
    )


FREE_MODEL_MAX_TOKENS = 8000
PAID_MODEL_MAX_TOKENS = 16000

# MACRO_KEYS is imported from `week` rather than defined here: `SlotSpec.
# skip_estimate` validates against it, and the module dependency runs
# planner -> week, so the tuple has to live at the bottom of that edge.
# Re-exported by this import so `from planner import MACRO_KEYS` — which
# ui_state and the tests use — keeps working.

# Everything that scales linearly with an ingredient's quantity — the budgeted
# four above, plus fibre.
#
# **`MACRO_KEYS` names the keys with a term in `calories ~= 4p + 4c + 9f`, not
# the keys with a target.** It answered both questions until fibre got a target
# of its own (`nutrition_engine.calculate_fiber_target_g`), and the split
# between the two is now the thing to keep straight:
#
# - **Identity operations walk `MACRO_KEYS`.** `derive_fat_g` solves the
#   equation, `apply_protein_floor` moves calories with protein at 4 kcal/g to
#   preserve it, and `DayRecipes.reject_untrimmable_macro_miss` bounces a
#   response whose calories don't reconcile. Fibre has no term in any of them —
#   it is already excluded from `net_carbs_g` by definition — so putting it
#   there would drop a fifth number into arithmetic with nowhere to put it.
# - **Proportional operations may walk `NUTRIENT_KEYS`.** A portion trim, a
#   recipe total and a per-slot share are all linear in quantity and care
#   nothing for the identity, which is what lets fibre take a briefed share of
#   a day without ever entering a budget check.
#
# So fibre now has a daily target, a per-slot share and a denominator on the
# telemetry header — and still no validator, because a single scale factor
# cannot change a macro ratio and rejecting a fibre-light response would cost a
# 30s-3min retry that nothing downstream could act on. See `split_targets`'
# fibre pass and `FIBER_TARGET_RULE`.
NUTRIENT_KEYS = MACRO_KEYS + ("fiber_g",)

WEEKEND_DAYS = {"Saturday", "Sunday"}

# Active prep-time ceilings, in minutes. Both the Pydantic validators and the
# prompt text read these: the model has to be *told* the same limit it is then
# judged against, or it gets rejected for breaking a rule it was never given.
# The weekend figure was previously only ever stated in the prompt, so a
# weekend recipe at 200 minutes passed validation while violating its own brief.
WEEKNIGHT_PREP_LIMIT_MINUTES = 30
WEEKEND_PREP_LIMIT_MINUTES = 180


def prep_limit_for(day: str) -> int:
    """The active prep ceiling that applies on `day`."""
    return WEEKEND_PREP_LIMIT_MINUTES if day in WEEKEND_DAYS else WEEKNIGHT_PREP_LIMIT_MINUTES


# Wall-clock ceiling, in minutes, for a day with no room for an unattended
# cook. Deliberately NOT a second reading of the two limits above: those count
# *active* minutes, and the whole reason a 4-hour braise reached a Tuesday is
# that it truthfully reported 25 of them. Active minutes are a claim on your
# attention; elapsed minutes are a claim on your presence, and a dish can pass
# one while failing the other. 90 leaves a weeknight room for a 30-minute
# active dish that then sits an hour in the oven, and rules out the braise.
WEEKNIGHT_ELAPSED_LIMIT_MINUTES = 90


def day_allows_long_cook(day: str, config: Optional[dict] = None) -> bool:
    """Whether `day` has the *hours* in it for a long, mostly unattended cook.

    The one notion of "a day with room to cook", read by both halves of a rule
    that used to exist on only one of them: `favorite_fits_day` (which slots a
    saved `long_oven_cook` favourite may take) and
    `reject_misplaced_long_cook` (which days a *generated* one may land on). A second copy would let the
    two disagree about a Tuesday, and the favourite path would go on being the
    only one enforced.

    **A location's own declaration wins, in both directions.**
    `location_rules.<location>.allows_long_cook` is the answer when the day's
    `base_schedule` location states one, so a WFH Tuesday qualifies — a slow
    cooker started at 8am on a work-from-home day is genuinely fine — and a
    Saturday declared `Outing` does not, because you cannot start a braise on
    a day you are out. That the shipped config *narrows* a weekend day this
    way is the point rather than a side effect: the rule this replaces keyed
    on the calendar, and the complaint against it was that the calendar is not
    where you are.

    Falls back to the weekend test whenever the location says nothing. That is
    three cases — an absent `base_schedule`, an unknown location, and a
    `location_rules` entry with no `allows_long_cook` key — and they collapse
    into one here because `week.location_rule` already collapses the first two
    into `{}`, which is the whole reason to call it rather than reach into
    both config sections again. So a config predating this key plans
    byte-identically to before it existed, which is the same tolerance
    `inventory_instruction` extends to an empty pantry list.

    Note this deliberately does not touch `prep_limit_for`, whose
    weeknight-versus-weekend split stays exactly as it was. Widening the
    *active* ceiling on a WFH day would say you have three hands-on hours on a
    working Tuesday, which is a different and false claim.
    """
    declared = location_rule(config or {}, day).get("allows_long_cook")
    if declared is None:
        return day in WEEKEND_DAYS
    return bool(declared)


def long_cook_days(days: List[str], config: Optional[dict] = None) -> List[str]:
    """Which of `days` allow a long cook, in the order given.

    The prompt side of `day_allows_long_cook`: the model has to be told the
    same days it is then judged against, which is the rule
    `WEEKEND_PREP_LIMIT_MINUTES` already learned the hard way — a weekend
    recipe at 200 minutes once passed validation while violating its own
    brief, because the limit was stated in the prompt and nowhere else.
    """
    return [day for day in days if day_allows_long_cook(day, config)]

# Stops the model from hitting a high protein budget by linearly scaling a
# single low-density ingredient (6 eggs, 500g yoghurt) instead of composing a
# realistic dish. Multiple dense protein sources combined instead.
PORTION_DENSITY_GUARD = (
    "- NEVER scale a single ingredient beyond standard human eating portions "
    "to hit a protein target: max 2-3 eggs per serving, max 150g yoghurt per "
    "serving, max 2 slices of bread/toast per serving, max 1 standard tin "
    "(90-125g) of sardines or mackerel per serving, max ~200g cooked "
    "meat/poultry/fish per serving, max 45g (about 1.5 scoops) protein "
    "powder per serving.\n"
    "- If a slot's protein target (e.g. >45g) is higher than one dense "
    "source can naturally provide at a standard portion, do NOT reach it by "
    "multiplying a single ingredient (never output 6 eggs, 500g yoghurt, or "
    "100g+ protein powder to hit a number). Instead COMBINE multiple dense "
    "protein sources at realistic portions each, e.g. whey protein powder + "
    "Greek yoghurt + hemp seeds, or chicken breast + edamame, or eggs + "
    "smoked salmon. The target is reached by composing several complementary "
    "sources, not by inflating one.\n"
)

# Share of the day each meal type gets when splitting targets across slots.
# Only the ratios matter — they're normalised over whichever slots are
# actually being cooked, so a day with no snack redistributes its share.
DEFAULT_MEAL_WEIGHTS = {"breakfast": 0.30, "lunch": 0.30, "dinner": 0.30, "snack": 0.10}

# How a week's cuisines are laid out when config.json doesn't say: two
# contiguous blocks, four days then three, rather than a different country
# every night. Overridden by `planning_rules.cuisine_block_pattern`, and
# scaled to the days actually cooked by `cuisine_block_sizes`. Defined up here
# rather than beside `pick_cuisine_blocks` because `PlanningRules`' default
# factory reads it at class-definition time.
DEFAULT_CUISINE_BLOCK_PATTERN = [4, 3]

# generate_week_plan's generation order: one API call per meal type, across
# every day it's cooked, rather than one call per day across every meal type.
# Dinner comes before lunch specifically so the one cross-meal-type leftover
# week.leftover_meal_type_error allows — a lunch eating a dinner's leftovers —
# always has its source already generated by the time its own stage runs.
# Order otherwise doesn't matter for correctness, only for how early a
# variety/cascading benefit shows up.
MEAL_TYPE_PRIORITY = ["breakfast", "dinner", "lunch", "snack"]


def meal_type_order(config: dict) -> List[str]:
    """generate_week_plan's per-run meal-type sequence.

    Filters MEAL_TYPE_PRIORITY down to meal types this config actually uses,
    then appends any config-defined meal type MEAL_TYPE_PRIORITY doesn't know
    about (in config's own order) — a custom meal type still gets generated,
    just without a considered position in the sequence.
    """
    known = meal_types(config)
    ordered = [meal_type for meal_type in MEAL_TYPE_PRIORITY if meal_type in known]
    ordered += [meal_type for meal_type in known if meal_type not in ordered]
    return ordered


# Injected only into the dinner call: generating all 7 dinners in one request
# gives the model full-week visibility, which a per-day call never had — this
# is the rule that visibility is for.
#
# The consecutive-nights clause is the half that survives cuisine blocking
# (see `build_cuisine_continuity_rule`). Once four nights share one cuisine,
# "no more than two of any protein across the week" is no longer enough on its
# own: the model's easiest way to write four Greek dinners is lamb, lamb,
# chicken, chicken, which honours the count and still reads as the same meal
# twice. Variety within a block has to come from the protein rotating night to
# night, so the rule that blocking makes load-bearing is stated explicitly
# rather than left implied by the cap.
DINNER_VARIETY_RULE = (
    "- You are generating all 7 dinners for the week in this one request, so "
    "you can see the whole week at once. Maximize variety in main proteins: "
    "do not repeat poultry, beef, or any single main protein as the primary "
    "protein in more than two dinners across the week, and never use the same "
    "primary protein on two consecutive nights — not even when consecutive "
    "nights share a cuisine.\n"
)

# Injected only into the dinner call, and only when Sunday prep is enabled.
# LONG_OVEN_COOK_RULE (below `build_generation_rules`, shared by both axes)
# says WHEN a dish qualifies to set the flag — on its own that reads as
# permission, not a request, and the model's easiest path is seven quick
# stovetop dinners that all legitimately satisfy "false for anything needing
# active stovetop attention." This is what actually asks for one. Scoped to
# the dinner axis for the same reason as DINNER_VARIETY_RULE: only this call
# sees all 7 nights at once and can choose which day gets it; gated on
# `enable_sunday_prep` because a week with the feature off has no use for what
# this produces, and `generate_sunday_prep_session` already no-ops without a
# candidate. Asking for exactly one, and ruling the rest out, is what keeps
# `generate_sunday_prep_session`'s candidate list small and genuinely
# "aggregate this one batch" rather than every braise-ish dinner qualifying.
def build_batch_roast_rule(config: dict, days: List[str]) -> str:
    """Ask for exactly one long oven cook this week, on a day that can take it.

    Was a bare constant preferring "a weekend day, if one is listed below",
    which was the right instinct and unenforceable: nothing rejected a model
    that put a 4-hour braise on a Tuesday while truthfully reporting 25 active
    minutes. Now that `reject_misplaced_long_cook` does, this has to name the
    days that check will accept — the same told-the-rule-you-are-judged-against
    coupling `prep_limit_for` has with `build_slot_brief`.

    **Emits nothing when no day in scope can take one**, which is the half
    that would otherwise be a guaranteed retry: asking for a long cook the
    validator is certain to reject burns the full 30s—3min call to discover a
    contradiction already visible in the config. A week entirely of Office and
    Outing days simply gets seven ordinary dinners, and
    `generate_sunday_prep_session` already no-ops without a candidate.
    """
    permitted = long_cook_days(days, config)
    if not permitted:
        return ""
    return (
        "- Make exactly ONE dinner this week a genuinely long (60+ minutes), "
        "mostly hands-off oven roast/bake or slow-cooker/braise, and set its "
        "long_oven_cook to true (per the long_oven_cook rule below). It must "
        f"fall on one of these days: {', '.join(permitted)} — those are the "
        "days with the hours in them. Every other dinner stays "
        "normal-length, active-cook, and must NOT be marked long_oven_cook.\n"
    )

# The anchor-aware sibling of build_batch_roast_rule, above. That rule leaves day
# choice to the model; this one is used instead whenever `week.spread_batch`
# has already picked the day (config["long_cook_anchor"], set only by
# ui_generation.apply_batch_selections) — the model just executes the choice,
# communicated per-slot via LONG_COOK_ANCHOR_SLOT_DIRECTIVE. build_batch_roast_rule
# itself stays wired to its old gate for the CLI's "enable_sunday_prep but no
# anchor" case — see generate_meal_type_week's gating below.
BATCH_ROAST_ANCHOR_RULE = (
    "- One dinner below is already marked as this week's long cook (see its "
    "own line for the [LONG COOK] directive) — make THAT one, and only that "
    "one, a genuinely long (60+ minutes), mostly hands-off oven roast/bake "
    "or slow-cooker/braise, and set its long_oven_cook to true (per the "
    "long_oven_cook rule below). Every other dinner stays normal-length, "
    "active-cook, and must NOT be marked long_oven_cook.\n"
)

LONG_COOK_ANCHOR_SLOT_DIRECTIVE = (
    "[LONG COOK: this is the week's long, hands-off oven roast/bake or "
    "slow-cooker/braise — see the long-cook rule above. Set long_oven_cook "
    "to true.]"
)

# Bulk-prep's own whole-week rule, gated on config["bulk_prep_anchor"] — set
# only by ui_generation.apply_batch_selections, so this never fires for CLI
# or regeneration (bulk_prep didn't exist before this feature, so there is no
# prior CLI behaviour to preserve here).
BULK_PREP_RULE = (
    "- One meal below is already marked as this week's bulk-prep batch "
    "(see its own line for the [BULK PREP] directive) — make THAT one, and "
    "only that one, well suited to big-batch cooking and multi-day storage "
    "(a soup, stew, curry, chilli, casserole or similar — it does not need "
    "to be a long or hands-off cook) and set its bulk_prep_friendly to true. "
    "It is cooked once ahead of the week and reheated on each day that eats "
    "it, so it must hold up to several days refrigerated and travel well in "
    "a container. Every other meal must NOT be marked bulk_prep_friendly.\n"
)

BULK_PREP_ANCHOR_SLOT_DIRECTIVE = (
    "[BULK PREP: this is the week's bulk-prep batch — see the bulk-prep "
    "rule above. Set bulk_prep_friendly to true.]"
)

# Injected into the breakfast call when more than one breakfast is a shake.
# The style description in config.json already lists the base and the pools to
# draw from; what one slot's brief cannot say is what the *other* shakes did,
# which is exactly what the whole-week call can. Phrased as "spread the pools
# evenly" rather than "every ingredient must be unique" because the pools are
# small (three fruits, three seeds) and a rule that can't be satisfied is one
# the model resolves by ignoring the constraint entirely.
# Note what counts as "the base" here, and what doesn't. The leafy green, the
# frozen vegetable and the fruit are named as part of it alongside the powder,
# because the shake style makes all three mandatory in every shake — and a
# rotation rule whose job is to make two drinks differ will reach for whatever
# it is allowed to drop. Left in the "secondary components" pool they would be
# exactly that: the cheapest thing to remove from one of the two shakes, which
# is the one outcome making them mandatory was meant to prevent.
# The fruit is the subtlest of the three, because it is *also* the first thing
# the very next clause tells the model to rotate ("no two may share the same
# combination of fruit, seeds, nuts and flavouring"). Naming it as base is
# what makes that a rule about WHICH fruit rather than WHETHER one — without
# it, dropping the fruit entirely is the single easiest way to satisfy the
# rotation clause, and a shake with a leafy green and frozen broccoli in it
# and no fruit at all is barely drinkable.
SHAKE_ROTATION_RULE = (
    "- More than one breakfast below is a protein shake. Keep the mandatory "
    "base in every one — protein powder, creatine, water, a leafy green, a "
    "frozen vegetable and a fruit, none of which may ever be dropped to make "
    "two shakes differ — and rotate the secondary components so no two shakes "
    "this week are the same drink: no two may share the same combination of "
    "fruit, seeds, nuts and flavouring, and the listed options must be spread "
    "as evenly as possible across the week rather than one favourite "
    "repeating. Varying WHICH green, WHICH vegetable or WHICH fruit between "
    "shakes is encouraged; omitting any of the three is not. Give each shake "
    "its own distinct name — a reworded name over identical ingredients is a "
    "repeat.\n"
)

# The per-slot half of the same rule, sent by both generation axes (a single
# regenerated shake gets it too, where the week-level rule above has no other
# shakes in the call to talk about).
SHAKE_SLOT_DIRECTIVE = (
    "[Protein shake: keep the base exactly at its fixed gram amounts (protein "
    "powder, creatine, water) — never scale the protein powder up to hit the "
    "budget; add a Protein Boost ingredient instead. A leafy green (20-30g), "
    "a raw frozen vegetable (50-80g) and one Fruit Fusion item are also "
    "mandatory and must appear in this shake: together they cost about 50-55 "
    "kcal, so there is no budget in which they don't fit. Vary only the "
    "secondary components — pick a fruit/seed/nut/spice combination no other "
    "shake this week uses.]"
)

# Standing rule for both axes. Variety (above) is about *foods*; this is about
# *variants of one staple*, and the two pull in opposite directions unless the
# prompt says which is which — hence the explicit "this is not the variety
# rule" clause. Every duplicate it names was observed on a real week's
# shopping list; `shopping.CANONICAL_INGREDIENTS` cleans up the ones that
# reach the list anyway, but a staple never duplicated is one nothing has to
# merge afterwards.
PANTRY_CONSOLIDATION_RULE = (
    "- Pantry consolidation & zero waste: minimize redundant ingredient "
    "varieties across the week. Standardize on a single staple variant — only "
    "ONE type of cottage cheese, ONE type of canned sardines, ONE type of "
    "mustard, ONE vinegar, ONE cooking oil, ONE yoghurt — and never introduce "
    "a minor variation of a base pantry staple already used elsewhere this "
    "week. Where a meal needs part of a perishable pack (fresh herbs, a tin, "
    "a bag of spinach), reuse that same item in another meal rather than "
    "buying a second kind of it. This is about variants of one staple, not "
    "about the food variety asked for above: keep varying the vegetables, "
    "proteins and spices — just don't buy three kinds of mustard.\n"
)


# Terms that mark a dominant protein as seafood, for the whole-week cap in
# `sourcing.max_seafood_meals_per_week`. Matched against the *highest-protein
# ingredient* only (see `is_seafood_meal`), never a scan of every name — a
# scan counts a Thai dinner's tablespoon of fish sauce, or a dashi stock, as a
# seafood meal, and the cap would then be spent by dishes that contain no fish.
SEAFOOD_TERMS = (
    "anchov", "barramundi", "calamari", "clam", "cod", "crab", "fish",
    "flathead", "haddock", "hake", "halibut", "herring", "kipper", "lobster",
    "mackerel", "mussel", "octopus", "oyster", "prawn", "salmon", "sardine",
    "scallop", "seafood", "shrimp", "snapper", "squid", "trout", "tuna",
    "whiting",
)


def _sourcing_day_split(
    days: List[str], available_days: Optional[List[str]]
) -> Tuple[List[str], List[str]]:
    """Split `days` into (restricted, open) against a sourcing day-list.

    `None` means every day is open (unrestricted, the old bare `True`); `[]`
    means every day is restricted (the old bare `False`). Order follows
    `days`, which both callers already carry in week order.
    """
    if available_days is None:
        return [], list(days)
    available_set = set(available_days)
    return (
        [day for day in days if day not in available_set],
        [day for day in days if day in available_set],
    )


def build_sourcing_rule(config: dict, days: List[str]) -> str:
    """What the shops near this kitchen actually stock, and on which days.

    The complaint this answers is a recipe that reads as ordinary and is
    unbuyable: mustard greens, a specialty cut, a fresh whole fish. Wording is
    deliberately a *substitution* instruction rather than a prohibition —
    "swap it for what the supermarket has" keeps the dish, where "don't use
    it" invites the model to abandon the cuisine or, worse, to name the
    unavailable item anyway because the recipe needs one. Same
    priority-not-constraint shape as `inventory_instruction`.

    `days` is the cook days this call's prompt covers, in week order —
    `generate_meal_type_week` passes every day the meal type is cooked (often
    all seven), `generate_day` passes its single day. A call whose days
    straddle both an open day (say, a Saturday market run) and a restricted
    one gets a day-scoped sentence naming exactly which nights each rule
    applies to, rather than the unconditional wording a fully-open or
    fully-restricted call still gets.

    Emits nothing when `sourcing` is absent or says nothing is constrained, so
    an older checkout's prompt is unchanged. The hard half of this constraint
    stays `dietary_rules.banned_ingredients`, which a validator enforces; a
    specific offender belongs there, not here.
    """
    sourcing = config.get("sourcing") or {}
    shops = sourcing.get("supermarkets") or []
    regional = config.get("regional") or {}
    where = ", ".join(
        part for part in (regional.get("state"), regional.get("country")) if part
    )

    specialty_restricted, specialty_open = _sourcing_day_split(
        days, sourcing.get("specialty_grocers_available_days")
    )

    lines = []
    if shops or specialty_restricted:
        # "Coles, Woolworths or Aldi" — an "or" list, because the model is
        # picking one shop to imagine, not visiting all three.
        shop_list = f"{', '.join(shops[:-1])} or {shops[-1]}" if len(shops) > 1 else "".join(shops)
        at_shops = f" at {shop_list}" if shop_list else ""
        in_region = f" in {where}" if where else ""
        lines.append(
            "- Ingredient sourcing: every ingredient must be buyable on one "
            f"ordinary shop{at_shops}{in_region}. "
        )
        if specialty_restricted and specialty_open:
            lines.append(
                "A specialty grocer (Asian grocer, continental deli, "
                "health-food shop, fishmonger or farmers' market) is also "
                f"reachable on {', '.join(specialty_open)} — ingredients "
                "needing one are fine for those day(s) only. On "
                f"{', '.join(specialty_restricted)}, avoid anything that "
                "needs one — specialty leafy greens (mustard greens, gai lan, "
                "tatsoi, amaranth), hard-to-find fresh produce (galangal, "
                "curry leaves, fresh yuzu, banana blossom), specialty butcher "
                "cuts and imported niche products. "
            )
        elif specialty_restricted:
            lines.append(
                "There is no Asian grocer, continental deli, health-food shop, "
                "fishmonger or farmers' market in reach, so avoid anything that "
                "needs one — specialty leafy greens (mustard greens, gai lan, "
                "tatsoi, amaranth), hard-to-find fresh produce (galangal, curry "
                "leaves, fresh yuzu, banana blossom), specialty butcher cuts and "
                "imported niche products. "
            )
        lines.append(
            "Where a cuisine traditionally calls for something like that, "
            "substitute the closest ingredient a mainstream supermarket stocks "
            "and put the SUBSTITUTE in the recipe — do not name the original "
            "and do not abandon the cuisine over it.\n"
        )

    seafood_restricted, seafood_open = _sourcing_day_split(
        days, sourcing.get("fresh_seafood_available_days")
    )
    if seafood_restricted and seafood_open:
        lines.append(
            "- Fresh fish, whole fish and fresh shellfish are only reliably "
            f"available here on {', '.join(seafood_open)}. On "
            f"{', '.join(seafood_restricted)}, any seafood must be the tinned "
            "or frozen kind a supermarket always has (tinned tuna, salmon, "
            "sardines or mackerel; frozen fish fillets or prawns).\n"
        )
    elif seafood_restricted:
        lines.append(
            "- Any seafood must be the tinned or frozen kind a supermarket "
            "always has (tinned tuna, salmon, sardines or mackerel; frozen "
            "fish fillets or prawns). Fresh fish, whole fish and fresh "
            "shellfish are not reliably available here.\n"
        )

    return "".join(lines)


def build_seafood_limit_rule(
    config: dict, meal_type: str, slot_count: int, already_used: int = 0
) -> str:
    """The whole-week seafood cap, stated for the meal type about to generate.

    Only the week axis (`generate_meal_type_week`) sends this, and only ever
    with the allowance *remaining* after every earlier meal type's actual
    output — `generate_week_plan` counts what each stage returned and spends
    the cap in `MEAL_TYPE_PRIORITY` order, the same seed-then-extend pattern
    `avoid_proteins` already uses across stages. Dinner is first in that
    order, so it gets first claim, which is where a seafood meal is wanted if
    the week is only having one.

    `regenerate_single_day`/`regenerate_single_meal` deliberately send nothing:
    a single replaced meal has no week in front of it to count against, and a
    cap restated there would forbid seafood in the very slot being fixed.
    """
    sourcing = config.get("sourcing") or {}
    cap = sourcing.get("max_seafood_meals_per_week")
    if cap is None:
        return ""

    remaining = max(0, cap - already_used)
    # "lunch" is the one meal type a bare "s" gets wrong, and "these 7 lunchs"
    # in a prompt reads as sloppiness the model is free to match.
    suffix = "es" if meal_type.endswith(("ch", "sh", "s", "x")) else "s"
    plural = f"{meal_type}{suffix}" if slot_count != 1 else meal_type
    if remaining == 0:
        used = (
            " — this week's whole allowance is already taken by earlier meals"
            if already_used
            else ""
        )
        return (
            f"- Seafood: none of these {slot_count} {plural} may have seafood as "
            f"its main protein{used}. Use meat, poultry, eggs, dairy or plant "
            "proteins instead.\n"
        )
    if remaining >= slot_count:
        return ""
    return (
        f"- Seafood: at most {remaining} of these {slot_count} {plural} may have "
        "seafood as its main protein — fresh seafood is hard to buy here, so it "
        "is a treat rather than a staple. Every other one uses meat, poultry, "
        "eggs, dairy or plant proteins.\n"
    )


def build_diet_style_rule(config: dict) -> str:
    """Standing dietary-approach guidance — Mediterranean, Fast 800, DASH,
    the Total Wellbeing Diet — layered on top of cuisine, not instead of it.

    A cuisine is a flavour tradition (`style_rule`, one per day/block); a diet
    style is a food-selection philosophy that applies regardless of which
    tradition is cooking, so a Korean dinner can be Mediterranean-principled
    and a Fast 800 day at the same time — exactly the stacking a single
    `cuisines` pick can't express. Selection lives in
    `dietary_rules.active_diet_styles`, catalog text in `diet_styles` (see
    config/meals.json): same active-list/catalog split as cuisines, minus the
    block rotation, since a diet style is followed for the whole week rather
    than picked per day.

    **This rule still carries no numeric lever**, and that has not changed
    now that `diet_style_calorie_ceiling` exists: the ceiling is applied
    inside `hydrate_dynamic_targets`, where the day's calories are already
    decided, and never restated here. Telling the model a number the budget
    it was handed already reflects is how a model starts optimising for the
    number instead of the food — the same failure `FIBER_TARGET_RULE`'s
    second sentence exists to head off. What this rule sends about Fast 800
    is what it always sent: simple, lean, low-added-fat dishes, inside
    whatever budget the day was given.

    Empty when no active styles are configured (the default), so the prompt
    is byte-identical to before this feature existed — same convention as
    `build_avoid_rules`/`inventory_instruction`.
    """
    catalog = config.get("diet_styles") or {}
    active = config["dietary_rules"].get("active_diet_styles") or []
    lines = "".join(
        f"  - {catalog[key]['label']}: {catalog[key]['principles']}\n"
        for key in active
        if key in catalog
    )
    if not lines:
        return ""
    return (
        "- This week also follows these standing dietary approaches, in "
        "addition to (not instead of) each meal's cuisine — cuisine is the "
        "flavour tradition, a dietary approach is what to prioritize within "
        "it, and a dish should satisfy both at once:\n" + lines
    )


def diet_style_calorie_ceiling(config: dict) -> Optional[float]:
    """The lowest whole-day calorie ceiling any active diet style declares.

    Fast 800's real-world hook is a calorie ceiling, and until now it was
    expressed only as food-selection guidance in `build_diet_style_rule` —
    "simple, lean, low-added-fat dishes" inside whatever budget the day was
    already given. That was the right call while the alternative on offer was
    a second config knob sitting *beside* `weekly_schedule`, which is exactly
    the competing source of truth `hydrate_dynamic_targets` exists to prevent.

    **A ceiling is not that, and the difference is idempotence.** The reason
    an adjustment was refused is that hydration runs twice — the UI hydrates
    for its own live preview and generation hydrates the same config again —
    and anything that *shifts* a number shifts it twice. `min()` does not:
    applying the ceiling to an already-capped day returns the same figure,
    which is the identical property that made a stated target safe to take
    verbatim rather than unwinding the training uplift off it.

    **Lowest wins when several styles declare one.** A ceiling is a bound
    somebody chose to live inside, so two active styles both declaring one are
    two bounds and only the tighter is actually being kept. Averaging them
    would produce a number neither style asked for — the same reason
    `reconcile_adaptive_tdee` chooses one TDEE rather than blending two.

    None — no active style, or none of the active ones declaring a ceiling —
    is the default and every day plans exactly as it did before this existed.
    """
    catalog = config.get("diet_styles") or {}
    active = (config.get("dietary_rules") or {}).get("active_diet_styles") or []
    ceilings = [
        float(catalog[key]["calorie_ceiling"])
        for key in active
        if key in catalog and catalog[key].get("calorie_ceiling") is not None
    ]
    return min(ceilings) if ceilings else None


class RejectionEntry(BaseModel):
    """One "why did you regenerate this" answer — see CLAUDE.md's "Rejection
    capture". Same field-naming convention `AdherenceEntry` below uses for the
    sibling signal, since the two are stored separately on purpose but
    describe adjacent moments — a rejection is a "no" to a suggestion before
    it ever became the plan, a mark is what happened to the plan.

    `date` is when the rejection was recorded (today), not the weekday the
    slot belongs to — that's `slot_id`'s job, and the two answer different
    questions: "when did the user say this" vs. "which meal were they
    reacting to".
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    slot_id: str
    recipe_name: str
    reason: Literal["too_much_prep", "dont_fancy_it", "had_it_recently", "wrong_for_slot"]
    marked_at: str


# `RejectionEntry.reason` -> the phrase both the UI's chip labels and the
# prompt rule use, so the two can't drift apart — same reasoning
# `TRAINING_NOTE_PREFIXES` already documents for its own UI/prompt pairing.
REJECTION_REASON_LABELS = {
    "too_much_prep": "too much prep",
    "dont_fancy_it": "didn't fancy it",
    "had_it_recently": "had it recently",
    "wrong_for_slot": "wrong fit for that meal",
}


# `RejectionEntry.reason` -> what a *run* of that answer says about how to
# cook generally, as opposed to `REJECTION_REASON_LABELS`, which says what one
# entry was about. The split mirrors the two halves of `build_rejection_rule`:
# a label names a dish's veto, this names a standing preference.
REJECTION_REASON_GUIDANCE = {
    "too_much_prep": "favour simpler methods and shorter active prep",
    "dont_fancy_it": (
        "push the flavour profile and protein further from the usual — these "
        "suggestions are not landing"
    ),
    "had_it_recently": (
        "reach further back in the rotation; these suggestions are repeating "
        "themselves"
    ),
    "wrong_for_slot": (
        "match the meal type more carefully — its weight, format and time of "
        "day"
    ),
}

# How many entries sharing a reason count as a pattern rather than a
# coincidence. Two is a pair of unrelated bad nights; three is a preference
# being stated. A module constant rather than a third `planning_rules` key:
# the two windows are the policy this feature exists to let you choose, and
# the threshold is arithmetic in service of them.
REJECTION_REASON_SIGNAL_MIN = 3


def rejection_age_days(entry: dict, today: date) -> Optional[int]:
    """How many days ago `entry` was recorded, or None if it can't be dated.

    An undated or malformed entry is un-ageable, and every caller here reads
    None as "keep it" — the same tolerant direction `recipe_last_scheduled`
    takes for an undated history row, and the opposite reading for the
    opposite reason: there, an unknown date must not re-serve a favourite;
    here, it must not silently discard a preference the user actually stated.
    """
    raw = entry.get("date")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return None
    return (today - when).days


def active_rejections(
    rejections: List[dict], config: Optional[dict], today: Optional[date] = None
) -> List[dict]:
    """The entries still inside their own reason's `rejection_decay_days`.

    The dish-naming half of the decay: an entry past its window stops being
    named as a dish to avoid. It does *not* stop counting toward the reason
    tally, which runs on the longer `rejection_reason_window_days` — see
    `build_rejection_rule`.

    A reason absent from the config dict, and an entry that cannot be dated,
    both survive. Neither is reachable from the app itself (`reason` is a
    `Literal` and the UI stamps `date` from the clock), so both mean a
    hand-edited file, and dropping a real signal over an unrecognised field
    is the worse failure.
    """
    today = today or date.today()
    windows = planning_rule(config, "rejection_decay_days")
    kept = []
    for entry in rejections:
        window = windows.get(entry.get("reason"))
        age = rejection_age_days(entry, today)
        if window is None or age is None or age < window:
            kept.append(entry)
    return kept


def recurring_rejection_reasons(
    rejections: List[dict], config: Optional[dict], today: Optional[date] = None
) -> List[str]:
    """Reasons given at least `REJECTION_REASON_SIGNAL_MIN` times recently.

    "Recently" is `rejection_reason_window_days` (180 by default), which is
    longer than any dish's own window on purpose — the point of the split is
    that the tally outlives the names it was counted from.

    Ordered by count, then by the reason's own decay window, so a tie is
    broken toward the more durable kind of statement rather than by dict
    order.
    """
    today = today or date.today()
    window = planning_rule(config, "rejection_reason_window_days")
    decay = planning_rule(config, "rejection_decay_days")
    counts: Dict[str, int] = {}
    for entry in rejections:
        age = rejection_age_days(entry, today)
        if age is not None and age >= window:
            continue
        reason = entry.get("reason")
        if reason in REJECTION_REASON_GUIDANCE:
            counts[reason] = counts.get(reason, 0) + 1
    return sorted(
        (r for r, n in counts.items() if n >= REJECTION_REASON_SIGNAL_MIN),
        key=lambda r: (-counts[r], -decay.get(r, 0), r),
    )


def build_rejection_rule(config: dict, today: Optional[date] = None) -> str:
    """Soft guidance from previously regenerated-away suggestions.

    Reads `config["rejected_preferences"]` — a list of `RejectionEntry` dicts
    injected by the three generation entry points the same way
    `select_nudge_foods` injects `nudge_foods`.

    Soft, like `build_diet_style_rule`/`build_sourcing_rule`: a rejection is a
    preference, not a banned ingredient, and a validator rejecting a response
    over it would cost a full retry for what is, at most, a repeated dish.
    Empty when there's nothing to say, so the prompt is byte-identical to
    before this feature existed — same convention every rule here follows.

    **Two signals, two windows, and that is the whole shape of the decay.**
    This shipped deliberately unbounded, to raise the policy question rather
    than settle it silently. The answer taken:

    - **The dishes to avoid decay per reason** (`active_rejections`, over
      `planning_rules.rejection_decay_days`). Honouring one dislike forever
      eventually starves the rotation — the same failure `next_choice`'s
      docstring documents from the other direction — and the four reasons
      expire at genuinely different rates, "had it recently" soonest.
    - **The recurring-reason hint counts over a longer window**
      (`recurring_rejection_reasons`), so a standing preference outlives the
      individual dishes that evidenced it.

    **The tally is now counted in Python, and that is a consequence of the
    split rather than a flourish.** The old rule handed the model the whole
    list and asked it to notice a repeated reason itself. Once the two halves
    have different windows the model only ever sees the shorter one, so it
    cannot do that counting — it would be weighing a subset while being told
    to weigh the whole. Naming the conclusion is the only honest option.

    `today` is the one seam onto the clock, so a test can age an entry
    without touching it — same shape as `select_favorite_assignments`.
    """
    rejections = config.get("rejected_preferences") or []
    if not rejections:
        return ""
    today = today or date.today()

    parts = []
    recent = active_rejections(rejections, config, today)
    if recent:
        lines = "".join(
            f'  - "{entry["recipe_name"]}" '
            f'({REJECTION_REASON_LABELS.get(entry["reason"], entry["reason"])})\n'
            for entry in recent
        )
        parts.append(
            "- The user has recently hit regenerate on these suggestions — "
            "avoid repeating the same dish:\n" + lines
        )
    reasons = recurring_rejection_reasons(rejections, config, today)
    if reasons:
        guidance = "".join(
            f"  - {REJECTION_REASON_GUIDANCE[reason]}\n" for reason in reasons
        )
        parts.append(
            "- Across the rejections recorded so far, the same objection "
            "keeps recurring. Treat this as a standing preference for this "
            "meal type, not just a note about the dishes above:\n" + guidance
        )
    return "".join(parts)


# What a mark on a planned meal says happened to it. Three answers rather
# than a boolean because the two failures are different in kind and the
# difference is the whole point of recording anything: a *skipped* meal is a
# day that came up short against a target it was planned to hit, while a
# *swapped* one is a day that was fed by something else entirely. A single
# "eaten?" flag collapses those into one "no", and a 7-day adherence readout
# built on it could not tell a missed dinner from a dinner out.
ADHERENCE_EATEN = "eaten"
ADHERENCE_SKIPPED = "skipped"
ADHERENCE_SWAPPED = "swapped"

ADHERENCE_STATUSES = (ADHERENCE_EATEN, ADHERENCE_SKIPPED, ADHERENCE_SWAPPED)

# `AdherenceEntry.status` -> the phrase the UI puts on a tooltip. Here beside
# the statuses rather than in `ui_theme.py` for the reason
# `REJECTION_REASON_LABELS` is here: the vocabulary and its wording are one
# fact, and a second copy in a widget module is free to drift from the
# `Literal` that actually constrains it.
ADHERENCE_STATUS_LABELS = {
    ADHERENCE_EATEN: "ate this",
    ADHERENCE_SKIPPED: "skipped this",
    ADHERENCE_SWAPPED: "ate something else",
}


class AdherenceEntry(BaseModel):
    """One "what actually happened to this meal" mark — see CLAUDE.md's
    "Whether the plan actually happened". `future-ideas.md`'s 5b named this
    type and this field list; only its storage changed, from a file of its
    own to a section of `data/adherence.json`.

    Keyed by `date` + `slot_id`, both of which are needed: a date holds four
    meals, and a `slot_id` is a weekday name (`"Thursday:dinner"`), so it
    repeats every seven days. That pair is what makes a re-mark a correction
    of one meal rather than a second opinion about it — the one thing
    separating this from `RejectionEntry`, which is an append-only log
    because two rejections on one slot are two events.

    Deliberately **not** an input to generation. A rejection is a preference
    about food, so `build_rejection_rule` sends it; a mark is a record of a
    day, and what to do with a run of skipped Thursdays is a product question
    (CHANGE-QUEUE.md's Insights item), not something to answer by quietly
    adding a fourth soft rule to every prompt.
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    slot_id: str
    status: Literal["eaten", "skipped", "swapped"]
    marked_at: str


class WorkoutCompletion(BaseModel):
    """One "I did this session" mark for a session the watch never recorded.

    **Only ever written for the sessions Garmin missed**, which is what
    shrank this from `future-ideas.md`'s proposed `data/workout_log.json`.
    v0.33.0's `activity_log` already holds what was actually done, and
    `nutrition_engine.match_recorded_sessions` reads a declared session
    against it — so storing a `completed` row for a session the watch saw
    would be a second answer to a question already answered, free to
    disagree with the first the moment a re-sync changed one of them.

    `session_id` is `"<time>:<type>"` (`"06:30:gym_hypertrophy"`), the same
    role `slot_id` plays for a meal: `date` says which day, this says which
    of that day's sessions. Time is part of it because a day may declare two
    sessions of one type, and `training_schedule` itself has no id to borrow
    — `PlannerState.pending_changes` already keys a session on `(day, time)`
    for the same absence.

    `source` is `"manual"` for everything this stores, and exists so a row
    stays legible beside the derived half it deliberately does not duplicate:
    a reader holding one of these knows a person asserted it, not a watch.
    """

    model_config = ConfigDict(extra="forbid")

    date: str
    session_id: str
    session_type: str
    completed: bool
    source: str
    marked_at: str


def workout_session_id(time: str, session_type: str) -> str:
    """`WorkoutCompletion.session_id` for a declared session.

    One function rather than an f-string at each call site, because the
    writer (the Daily View's mark button) and the reader (the view model
    that decides whether that button is already lit) have to agree on the
    spelling exactly, and a mark filed under a key nothing reads back is
    invisible rather than broken.
    """
    return f"{time}:{session_type}"


# --------------------------------------------------------------------------
# Prompt rules shared by both generation axes
#
# `generate_day` (one day, several meal types) and `generate_meal_type_week`
# (one meal type, several days) send almost the same rule list. They used to
# spell it out twice, and the copies drifted: the meal-type prompt silently
# lost LONG_OVEN_COOK_RULE, which left `long_oven_cook` at its schema default
# of False on every recipe the weekly path produced — and since
# `generate_sunday_prep_session` filters candidates on exactly that field, the
# entire Sunday prep feature quietly did nothing. The rules now live here, and
# only the genuinely axis-dependent prose is passed in.
# --------------------------------------------------------------------------

# Sets the field `generate_sunday_prep_session` selects its candidates by. A
# call that omits this rule can never contribute to a prep session.
# Fibre now has a target (`nutrition_engine.calculate_fiber_target_g`) and a
# per-slot share (`split_targets`), and this rule is what changed when it got
# one. It used to end "tracked for reporting only and has no target: never
# ... pick an ingredient to raise it", which was the right instruction while
# there was nothing to aim at and is exactly backwards now there is.
#
# **The clause that had to survive the rewrite is the one about trading.** A
# model told to hit a number will hit it out of whatever it has, and the four
# macros are the budget that actually is checked — a recipe that buys 6 g of
# fibre with 200 kcal of extra lentils passes no validator this app has.
# Fibre is bought by *substitution at constant macros* (wholegrain for
# refined, legumes in place of some starch, skins left on, the vegetable
# already in the dish chosen for its fibre), which is genuinely how the
# nutrient works, so the rule names the mechanism rather than only forbidding
# the alternative — "don't trade" alone leaves a model with a target and no
# permitted way to reach it, which is the shape of rule it drops.
#
# The field still has a 0.0 default, so a model that simply omits it produces
# a silently fibre-free week rather than an error, which is why asking for it
# explicitly is worth a prompt line rather than left to the schema
# description.
FIBER_TARGET_RULE = (
    "- Report fiber_g for every ingredient — the dietary fibre in that "
    "quantity, in grams, in addition to net_carbs_g (which already excludes "
    "it). Each meal below states a fibre target alongside its macro budget: "
    "reach it by CHOOSING higher-fibre forms of what the dish already "
    "contains — wholegrain rather than refined, legumes in place of some of "
    "the starch, vegetables and fruit with their skins, nuts and seeds "
    "already in the recipe — never by adding food on top. The calorie, "
    "protein, carb and fat budget is the hard constraint and fibre is a goal "
    "inside it: never trade any of the four against fibre, and if the two "
    "conflict, hit the macros and report the fibre you actually have.\n"
)

LONG_OVEN_COOK_RULE = (
    "- Set long_oven_cook to true only if this dish is a genuinely long "
    "(60+ minutes), mostly hands-off oven roast/bake or slow-cooker/braise "
    "— false for anything needing active stovetop attention, a quick "
    "recipe, or a no-cook dish, even one you're making in bulk.\n"
)

# The companion `long_oven_cook` never had, and the reason a 4-hour braise
# could land on a Tuesday while every check passed: `prep_time_minutes` counts
# only hands-on minutes, so a braise honestly reports 25 and clears the
# 30-minute weeknight ceiling it ought to fail. Neither number describes how
# long the dish actually takes, and `long_oven_cook` is a self-report the
# model is free to simply not set. This is the measured field the day rule
# below is enforced against.
ELAPSED_TIME_RULE = (
    "- Report total_time_minutes for every recipe: the whole wall-clock time "
    "from starting the dish to eating it, INCLUDING unattended time (oven, "
    "slow cooker, simmering, marinating, proving, chilling). It is always "
    "greater than or equal to prep_time_minutes, which counts only the "
    "hands-on minutes — a braise with 25 minutes of active work and 4 "
    "hours in the oven is prep_time_minutes 25, total_time_minutes 265. "
    "Where a dish has no unattended time the two are equal. Report it "
    "honestly: it is checked against the day's schedule, not against how "
    "impressive the dish sounds.\n"
)


def build_long_cook_day_rule(config: dict, days: List[str]) -> str:
    """Which of this call's days have the hours for a long, unattended cook.

    The prompt half of `reject_misplaced_long_cook`, and it exists because that
    validator is hard: a model rejected for breaking a rule it was never given
    burns a 30s—3min retry to discover a constraint one sentence would have
    stated. `WEEKEND_PREP_LIMIT_MINUTES` learned the same lesson from the
    other direction — it was once stated in the prompt and enforced nowhere,
    so a 200-minute weekend recipe passed validation while violating its own
    brief.

    Emits nothing when every day in scope allows one, which is the common case
    for a single weekend `generate_day` call and keeps that prompt unchanged.
    Says only the ceiling when none do, because naming an empty list of
    permitted days reads as a riddle.
    """
    permitted = long_cook_days(days, config)
    if len(permitted) == len(days):
        return ""
    ceiling = (
        "- On any other day keep total_time_minutes at or under "
        f"{WEEKNIGHT_ELAPSED_LIMIT_MINUTES} minutes and long_oven_cook "
        "false: those days have no room for a dish that sits for hours, "
        "however little hands-on work it needs.\n"
    )
    if not permitted:
        return (
            "- None of the days below has room for a long, mostly unattended "
            "cook. Keep every recipe's total_time_minutes at or under "
            f"{WEEKNIGHT_ELAPSED_LIMIT_MINUTES} minutes and long_oven_cook "
            "false — this is about the hours in the day, not the hands-on "
            "work, so a braise needing only 20 minutes of attention still "
            "does not fit.\n"
        )
    return (
        "- Only these days have room for a long, mostly unattended cook "
        f"(oven, slow cooker, braise): {', '.join(permitted)}.\n"
    ) + ceiling


def reject_misplaced_long_cook(pairs, config: Optional[dict]) -> None:
    """Raise if a long cook landed on a day with no hours in it.

    The hard half of a rule that was soft on the generated side and hard on
    the favourite side, which is how the two came to disagree: a saved
    `long_oven_cook` favourite could not take a Thursday, while a *generated*
    dinner could put a 4-hour braise there and pass every check on the way.
    `build_batch_roast_rule` states a preference and nothing enforced it,
    `prep_time_minutes` counts only the hands-on minutes so a braise clears
    the weeknight ceiling honestly, and `long_oven_cook` is the model's own
    self-report. Both halves now read `day_allows_long_cook`.

    **Two ways to fail, because one field alone catches only half of it.**
    `long_oven_cook` is set by a cooperative model and simply omitted by a
    careless one; `total_time_minutes` is the measured claim
    (`ELAPSED_TIME_RULE`) and is what catches the braise that never flagged
    itself. Either one over the line rejects, which is what makes the
    unflagged case reachable at all.

    **A batch anchor is exempt, and the whole rule would be wrong without
    it.** `apply_batch_selections` anchors both batches on day 1 — Monday, on
    the shipped config — but that food is cooked on **prep day**, the Sunday
    *before* the week starts (`week.PREP_DAY_INDEX`). The anchor's grid day is
    only where its leftover chain has to start, so judging it against Monday's
    schedule would reject the one dish in the week most deliberately given the
    hours, and would break the long-cook toggle outright. Same two slot ids
    `build_cook_event` already counts fridge days from.

    `total_time_minutes` is `None` on every recipe saved before it existed, and
    None is "unknown", never 0 — an unknown elapsed time falls through to the
    flag alone, the same pre-migration tolerance `history_styles` extends to
    old history entries.

    Raising is load-bearing, not defensive: instructor catches it and hands the
    model its own output back to retry.
    """
    exempt = prep_day_batch_slot_ids(config)
    for day, recipe in pairs:
        if slot_id(day, recipe.meal_type) in exempt:
            continue
        if day_allows_long_cook(day, config):
            continue
        elapsed = recipe.total_time_minutes
        if recipe.long_oven_cook:
            raise ValueError(
                f"{day}: \"{recipe.name}\" is marked long_oven_cook, but "
                f"{day} has no room for a long, mostly unattended cook. "
                "Replace it with a dish that is ready end to end in "
                f"{WEEKNIGHT_ELAPSED_LIMIT_MINUTES} minutes or less, or move "
                "the long cook to one of the days the rules list as having "
                "the hours for it."
            )
        if elapsed is not None and elapsed > WEEKNIGHT_ELAPSED_LIMIT_MINUTES:
            raise ValueError(
                f"{day}: \"{recipe.name}\" takes {elapsed} minutes end to "
                f"end, over the {WEEKNIGHT_ELAPSED_LIMIT_MINUTES}-minute limit "
                f"for {day}. Its {recipe.prep_time_minutes} minutes of "
                "hands-on work is not the problem — the hours it then sits "
                "are. Replace it with a dish that is ready end to end within "
                "the limit."
            )

def storage_spans(spec: WeekSpec, config: Optional[dict]) -> Dict[str, int]:
    """Slot id -> how many day-gaps that cook's food has to survive.

    Injected onto `config` as `storage_spans` by the three generation entry
    points, in memory only — the same channel `nudge_foods`,
    `inventory_ledger` and `target_locks` ride on, and covered by the standing
    rule that `save_config_keys` merges *named* keys rather than writing back
    whatever the config dict happens to hold.

    It is one dict read by two places that would otherwise each derive the
    span themselves: `build_storage_rule` (which tells the model what the slot
    needs) and `reject_short_storage_class` (which rejects a class that
    doesn't meet it). A model rejected against a figure different from the one
    it was given is a retry spent on the app's own inconsistency.

    **Measured from the day the food is actually cooked**, which for a batch
    anchor is prep day rather than its own grid slot (`week.PREP_DAY_INDEX`).
    That makes an anchor's span *longer*, not shorter, and it is the case that
    most needs the rule: `apply_batch_selections` anchors both batches on day
    1 while the pan was on the Sunday before the week started. CLAUDE.md
    records this exact off-by-one being fixed twice already — once as
    `max_day_index`, once in `storage_note` — so it is the third opportunity
    to reintroduce it.
    """
    prepped_ahead = prep_day_batch_slot_ids(config)
    return {
        cook.id: span_days(spec, cook.id, cook.id in prepped_ahead)
        for cook in spec.cook_slots()
    }


def build_storage_rule(config: Optional[dict], slot_ids: Iterable[str]) -> str:
    """Which of this call's slots have to keep, and for how long.

    The prompt half of `reject_short_storage_class`, and it exists for the
    reason `build_long_cook_day_rule` does: a model rejected for breaking a
    rule it was never given burns a 30s-3min retry to discover a constraint
    one sentence would have stated.

    **The ordering problem this solves.** `spread_batch` decides how far a
    batch reaches while the grid is built, and generation happens afterwards
    — so at the moment the span is chosen nothing knows whether the dish will
    turn out to be a rice tray bake. Planning short always would be safe and
    useless (no batch could reach past two days, which removes bulk cooking);
    planning long and validating afterwards throws the run away to discover
    something a sentence could have stated. Telling the model the span the
    slot needs is the third way out, and it is what this codebase already does
    twice for exactly this shape.

    **Emitted only when a span exceeds the short window**, so a week whose
    cooks are all eaten within it produces a byte-identical prompt to before
    this feature existed — the same convention every rule in
    `build_generation_rules` follows. The short window is the unclassified
    one, which is the shortest row in the table: any dish at all can survive a
    span inside it, so there is nothing to ask for.

    Slots are grouped by the figure they need rather than reduced to the
    largest, because the validator judges each slot against its own span and
    the prompt has to name the rule it is judged against. Keyed by slot id
    rather than by day because that is what `storage_spans` is keyed by, and
    because one `generate_day` call covers several meal types on one day.
    """
    spans = (config or {}).get("storage_spans") or {}
    short = fridge_day_gaps(None, config)
    needed: Dict[int, List[str]] = {}
    for value in slot_ids:
        span = spans.get(value, 0)
        if span > short:
            needed.setdefault(span, []).append(slot_label(value))
    if not needed:
        return ""
    clauses = "; ".join(
        f"{', '.join(group)} (still good {span} days after cooking)"
        for span, group in sorted(needed.items())
    )
    return (
        "- These are cooked once and eaten over several days, refrigerated in "
        f"between, so each has to still be good on the last of them: {clauses}"
        ". Build them as dishes that genuinely keep, and set storage_class to "
        f"the kind of dish each one is (one of: {', '.join(STORAGE_CLASSES)}). "
        "Do NOT build them on rice, pasta, noodles, couscous or other cooked "
        f"grains — those keep only {short} days whatever else is in the dish, "
        "and a dish labelled as something it is not will be rejected. Serve "
        "the grain freshly cooked alongside if you want one.\n"
    )


def reject_short_storage_class(pairs, config: Optional[dict]) -> None:
    """Raise if a dish cannot keep as long as its slot needs it to.

    The hard half of `build_storage_rule`, over the two-axis split
    `reject_misplaced_long_cook` already uses — `DayRecipes` from its
    context's single day, `MealTypeWeekRecipes` over its own day keys, this
    one shared function underneath so the two axes cannot disagree about a
    Tuesday.

    **A dropped field is caught here, not defaulted away.** `storage_class` is
    exactly the kind of self-report `is_sunday_prepped` was broken by: the
    anchor came back with both its flags False despite the per-slot directive
    telling the model to set one. So an omitted class resolves to the shortest
    window (`week.storage_window_for`), and a slot needing longer than that
    rejects — which is the model being told to answer the question rather than
    the app guessing on its behalf.

    **The batch anchors are emphatically not exempt**, unlike in
    `reject_misplaced_long_cook`. That rule exempts them because the *day*
    judgement is wrong for food cooked before the week started; this one is
    about the *window*, and their span is measured from prep day and is
    therefore longer. It is the case that most needs the rule.

    Raising is load-bearing, not defensive: instructor catches it and hands
    the model its own output back to retry.
    """
    spans = (config or {}).get("storage_spans") or {}
    for day, recipe in pairs:
        needed = spans.get(slot_id(day, recipe.meal_type), 0)
        if needed <= 0:
            continue
        allowed = fridge_day_gaps(recipe.storage_class, config)
        if allowed >= needed:
            continue
        raise ValueError(
            f"{day}: \"{recipe.name}\" is cooked once and eaten over the "
            f"next {needed} days, but {storage_class_label(recipe.storage_class)} "
            f"is only good for {allowed} days refrigerated. Either replace it "
            "with a dish that genuinely keeps that long and set storage_class "
            f"to what it is (one of: {', '.join(STORAGE_CLASSES)}), or, if "
            "this dish really does keep, set storage_class to the category it "
            "actually belongs to — do not leave it unset."
        )


# The three rules that differ by axis: whether "respect the style", "vary the
# ingredients" and "hit your budget" are scoped across one day's meal types or
# across one meal type's days.
DAY_STYLE_RULE = (
    "- Respect each meal's requested style and cuisine exactly. Where a "
    "cuisine is given it applies to that meal only — the other meals must "
    "draw on different culinary traditions so the day isn't one cuisine "
    "end to end.\n"
)
DAY_VARIETY_RULE = (
    "- Prioritize nutrient-dense whole foods: vary the vegetables, herbs/"
    "spices and protein sources across the day and minimize ingredient "
    "overlap between meals, the way a registered dietitian would design a "
    "menu — not just whatever hits the numbers with the fewest ingredients.\n"
)
DAY_BUDGET_RULE = (
    "- Each meal below carries its OWN macro budget. Hit that meal's "
    "budget — not a typical portion size for that meal, and not a whole "
    "day's worth. The budgets are already calculated and already add up "
    "correctly; do not recompute or redistribute them.\n"
)

WEEK_STYLE_RULE = (
    "- Respect each day's requested style and cuisine exactly. Different "
    "days should draw on different culinary traditions and styles so the "
    "week isn't the same dish repeated under different names.\n"
)
# WEEK_STYLE_RULE's replacement when the week is laid out in cuisine blocks
# (`build_cuisine_continuity_rule` returns something). The rule above tells the
# model consecutive days must differ in tradition, which is the direct
# opposite of what a 4/3 split asks for — left in place it invites the model
# to "fix" the repetition by quietly substituting a cuisine, and a day whose
# cuisine drifts is a day whose shopping list stops sharing anything with its
# neighbours, which is the entire point of blocking.
WEEK_CUISINE_BLOCK_STYLE_RULE = (
    "- Respect each day's requested style and cuisine exactly. The cuisines "
    "repeat across consecutive days on purpose (see the cuisine blocks "
    "below): variety inside a block comes from different dishes, proteins, "
    "vegetables and cooking methods, never from substituting a different "
    "cuisine. Do not rebalance the week's cuisines.\n"
)
WEEK_VARIETY_RULE = (
    "- Prioritize nutrient-dense whole foods: vary the vegetables, herbs/"
    "spices and protein sources across the days and minimize ingredient "
    "overlap between them, the way a registered dietitian would design a "
    "week's menu — not just whatever hits the numbers with the fewest "
    "ingredients.\n"
)
WEEK_BUDGET_RULE = (
    "- Each day below carries its OWN macro budget, already reduced for "
    "whatever that day already has fixed from other meal types this run. "
    "Hit that day's budget for this one meal — not a typical portion size "
    "for it — and do not recompute or redistribute the numbers.\n"
)
# Only the meal-type call returns a day-keyed object, so only it needs to say
# so. `MealTypeWeekRecipes.recipes` is a Dict[str, Recipe] for this reason —
# a missing day becomes a structural mismatch instructor can retry on.
WEEK_RESPONSE_SHAPE_RULE = (
    "- Respond with a JSON object whose keys are exactly the day names "
    "listed below and whose values are that day's recipe — do not add, "
    "omit, or rename a day.\n"
)


def build_avoid_rules(
    avoid_proteins: Optional[List[str]] = None,
    avoid_recipe_names: Optional[List[str]] = None,
) -> str:
    """The two "don't repeat what you just made" rules, or "" for an empty list.

    Both generation calls send these identically. An empty list emits nothing
    rather than an empty rule, so a cold start with no history produces a
    prompt with no dangling "avoid: ." line in it.
    """
    rules = ""
    if avoid_proteins:
        rules += (
            "- Avoid making any of these the primary protein again — they were used "
            f"recently: {', '.join(avoid_proteins)}.\n"
        )
    if avoid_recipe_names:
        rules += (
            "- Do NOT generate any of these exact dishes again under the same or "
            "a trivially reworded name — they already appear in recent history "
            f"and must not repeat: {', '.join(avoid_recipe_names)}.\n"
        )
    return rules


def build_generation_rules(
    config: dict,
    *,
    days: List[str],
    slot_ids: Iterable[str] = (),
    style_rule: str,
    variety_rule: str,
    budget_rule: str,
    extras: str = "",
    response_shape_rule: str = "",
) -> str:
    """The `Rules:` block sent by both generation calls, assembled once.

    Ordering is deliberate and shared: hard constraints (units, NOVA groups,
    banned ingredients) first, because they are the ones a validator will
    reject on, followed immediately by the sourcing rule — it is the soft half
    of the line above it (what cannot be *bought* rather than what will be
    *rejected*), and it reads as an afterthought anywhere else; then
    composition guidance — style, diet style, rejected preferences, variety,
    and the pantry consolidation rule that qualifies variety, in that order,
    because diet style is a second "what approach" axis read right after
    cuisine, rejected preferences is a third "what to avoid" axis read right
    after it (recently regenerated-away dishes, same soft-guidance shape as
    diet style), and consolidation only makes sense read as a limit on the
    sentence before it; then `extras` — the per-call blocks (dinner variety,
    cuisine blocks, shake rotation, avoid lists, pantry, fixed leftovers,
    batch cooking) that only sometimes apply; then the budget, which the
    model should read last and closest to the per-slot briefs in the user
    prompt.

    `config` supplies `dietary_rules` (including which diet styles are
    active) and the `diet_styles` catalog those keys resolve against;
    everything else is a caller decision, so this stays a pure string
    builder with no I/O and no model knowledge. `days` is passed straight
    through to `build_sourcing_rule` and `build_long_cook_day_rule`, the two
    rules here that need to know which cook days are in scope — one because
    what the shops stock varies by day, the other because what the *day*
    has room for does. `slot_ids` goes to `build_storage_rule`, which needs
    the *slots* rather than the days: how long a dish has to keep is a fact
    about one cook, and one `generate_day` call covers several on a day.
    Defaulted to empty so a caller with no spans in scope emits nothing.
    """
    dietary_rules = config["dietary_rules"]
    return (
        "Rules:\n"
        "- Use metric units only (grams) for all ingredient quantities.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Never use Group 4 ultra-processed ingredients.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients'])}.\n"
        f"{build_sourcing_rule(config, days)}"
        f"{style_rule}"
        f"{build_diet_style_rule(config)}"
        f"{build_rejection_rule(config)}"
        f"{variety_rule}"
        f"{PANTRY_CONSOLIDATION_RULE}"
        "- Keep single dairy/staple portions realistic (e.g., max 200-250g "
        "yoghurt or cottage cheese per serving).\n"
        "- Combine multiple complementary protein sources (e.g., yoghurt + "
        "protein powder, or eggs + lean meat) rather than scaling up a single "
        "low-density ingredient to meet high protein targets.\n"
        f"{PORTION_DENSITY_GUARD}"
        f"{extras}"
        f"{budget_rule}"
        "- All budgets are PER SERVING (one portion for one person). Report "
        "every ingredient's quantity_g and its calories/protein_g/net_carbs_g/"
        "fat_g for a SINGLE serving too. Do not multiply by the number of "
        "people or by any batch size — Python scales the recipe afterwards.\n"
        f"{FIBER_TARGET_RULE}"
        "- Leave servings and prep_notes at their schema defaults — Python "
        "fills those in.\n"
        f"{LONG_OVEN_COOK_RULE}"
        f"{ELAPSED_TIME_RULE}"
        f"{build_long_cook_day_rule(config, days)}"
        f"{build_storage_rule(config, slot_ids)}"
        f"{response_shape_rule}"
        "- Do not show your work, explain your reasoning, or narrate your "
        "process. Respond with the structured data only."
    )

# --------------------------------------------------------------------------
# AppConfig: config.json's schema, strictly validated at load time
# --------------------------------------------------------------------------
#
# Every section below used to be read with `config.get("section", {}).get(
# "key", SOME_DEFAULT)` scattered across planner.py/week.py, each call site
# free to pick its own fallback (or forget one). `load_app_config` now runs
# config.json through this model exactly once, at startup: a missing or
# mistyped field fails loudly there, before a single API call is made, and
# every field that survives is guaranteed present with a real value in the
# dict the rest of the app reads — so a call site indexes `config["key"]`
# directly instead of re-deciding what to do when it's absent.
#
# Sections that already have their own documented, per-item tolerance for
# malformed *entries* — `weekly_schedule.<day>.meal_overrides` (a typo in one
# meal must not cost the whole day, see `meal_overrides_for`) and
# `training_schedule` (an unknown day/type is logged and skipped, see
# `apply_training_adjustments`) — are typed loosely here (`Dict[str, Any]` /
# `List[Dict[str, Any]]`) so that existing per-item leniency still runs
# exactly as before. Strictness here is about the *shape* of config.json,
# not about re-implementing business rules that already live elsewhere.


class PlanningRules(BaseModel):
    """config.json's "planning_rules" object.

    Defaults match the numbers this section replaced when it was still a
    bare module constant (see git history) — an omitted key, or a
    config.json predating this section, resolves to the same behaviour as
    before it existed.
    """

    model_config = ConfigDict(extra="forbid")

    # 28 entries = 4 weeks of daily history, so recipe-name/style/protein
    # rotation has a full 4-week non-repeat window rather than 3.
    history_max_entries: int = Field(default=28, ge=1)
    protein_lookback_entries: int = Field(default=3, ge=0)
    # How many recent main proteins to name in the prompt. Long enough to
    # stop a week of chicken, short enough that a 7-day plan doesn't end up
    # banning everything the model knows by Friday.
    # ge=1, not ge=0: this is a negative slice bound (avoid_proteins[-window:])
    # and [-0:] is the whole list — "avoid nothing" would silently become
    # "avoid everything".
    protein_avoid_window: int = Field(default=6, ge=1)
    # Models compose plausible meals but size them badly, so portions are
    # corrected after the fact by scaling every quantity linearly. The clamp
    # stops a trim producing an absurd portion (a 30g breakfast, a 900g
    # steak).
    portion_trim_limits: Tuple[float, float] = (0.6, 1.6)
    portion_trim_deadband: float = 0.03
    # Smallest protein figure worth briefing a cooked meal at. A day's protein
    # is locked to the target weight (see `hydrate_dynamic_targets`), and a
    # weight-only split hands the 0.10-weighted snack ~14 g of it — a number
    # that produces a snack with no protein source in it at all. Muscle protein
    # synthesis is dose-dependent per *meal* rather than per day, so the floor
    # is what turns "144 g/day" into four meals that each actually carry
    # protein. Applied by `split_targets`, and only when the day can afford it.
    min_meal_protein_g: float = 35.0
    # Contiguous blocks of days sharing one cuisine, as a ratio scaled to the
    # days actually cooked (see `cuisine_block_sizes`). A single-element
    # pattern gives the whole week one cuisine; seven 1s restores the old
    # night-by-night rotation.
    cuisine_block_pattern: List[int] = Field(
        default_factory=lambda: list(DEFAULT_CUISINE_BLOCK_PATTERN)
    )
    # Floor on the share of a week's cook days `pick_cuisine_blocks` reserves
    # for `config.baseline_cuisines` (homestyle, modern_australian, pub_classic
    # in the shipped config) — the everyday-Western pool cuisine_affinities
    # already routes other cuisines toward for the *block after* theirs. This
    # is the same idea made a floor rather than a landing spot: enough of the
    # week's largest blocks are reserved for the pool to clear the share
    # before the rest rotates freely, so other cuisines read as the exception
    # that breaks up the week rather than the default. Only takes effect when
    # `baseline_cuisines` is non-empty — see that field's docstring.
    min_baseline_cuisine_share: float = Field(default=0.5, ge=0.0, le=1.0)
    # How recently a saved favourite may have been cooked before
    # `select_favorite_assignments` will schedule it again, per meal type.
    # Two different windows because the two rules mean different things: a
    # standing breakfast is *meant* to recur weekly, while a favourite lunch
    # is a break from rotation and wants a real gap behind it.
    #
    # Both must stay comfortably inside `history_max_entries` (a cap on
    # entries, one per cooked day) or `recipe_last_scheduled` cannot see far
    # enough back to answer, and a favourite that aged off the window looks
    # like one never cooked.
    favorite_reuse_days: Dict[str, int] = Field(
        default_factory=lambda: {"breakfast": 7, "lunch": 21, "dinner": 21}
    )
    # How many breakfasts one favourite covers. The point of a standing
    # breakfast is that it's the same one two mornings running, not a
    # different favourite each day — and it means one shop covers both.
    favorite_breakfast_slots: int = Field(default=2, ge=0)
    # How many dinners a week may be claimed by saved favourites. Unlike
    # breakfast this is a count of *distinct* favourites, not of days one
    # favourite covers — a standing dinner every Tuesday is not the point,
    # and `DINNER_VARIETY_RULE` exists precisely because dinner is where
    # repetition shows.
    #
    # It is a cap rather than lunch's "one per eligible slot" because dinner
    # is the one meal type `pick_cuisine_blocks` lays contiguous blocks over,
    # and `pin_recipe` blanks the cuisine of every slot it claims. Left
    # uncapped against a large catalog every dinner would be a pin, there
    # would be no block left to speak of, and the pantry overlap the blocks
    # exist for would go with it. Two leaves five generated dinners, which a
    # 4/3 `cuisine_block_pattern` can still do something with. Raise it if
    # you would rather cook the catalog than shop it cheaply.
    favorite_dinner_slots: int = Field(default=2, ge=0)
    # Ceiling `spread_batch` (week.py) spreads a bulk-prep/long-cook anchor
    # toward — not a promise: a batch that runs into the end of the week or an
    # already-claimed day settles for fewer servings.
    batch_target_servings: int = 6
    # How far past its own weighted share a meal's briefed budget may be pushed
    # by drift the cascade hands it (see `cap_to_weighted_share`). Generation
    # subtracts each stage's *actual* output from the day, so every earlier
    # meal's miss lands on whichever meal is generated last — and the protein
    # half of that miss is never recoverable, because `fit_recipe_to_budget`
    # scales a recipe to hit calories and cannot change a macro ratio.
    # Unbounded, that asks the 0.10-weighted snack to carry three meals'
    # accumulated shortfall. 1.75 leaves real room for ordinary drift to be
    # absorbed where it should be, and stops only the runaway case.
    max_meal_share_multiple: float = 1.75
    # How long a recorded rejection keeps naming its dish in the prompt, per
    # reason — the decay `build_rejection_rule` shipped without, deliberately,
    # so the policy could be chosen rather than defaulted into.
    #
    # Per reason rather than one window because the four are not the same kind
    # of statement, which is the same argument `favorite_reuse_days` makes for
    # its own split. "Had it recently" is self-resolving — the dish stops
    # having been had recently whether or not anything honours the entry — so
    # it expires soonest. "Wrong for that meal" is structural (a curry is
    # never breakfast) and barely decays at all. The other two sit between:
    # an effort judgement about a specific dish, and a taste one.
    #
    # A reason with no entry here never expires. That is the tolerant
    # direction, and the only way to reach it is a hand-edited file —
    # `RejectionEntry.reason` is a `Literal` of exactly these four — so the
    # alternative would be silently discarding a signal because the file knew
    # a word this dict did not.
    rejection_decay_days: Dict[str, int] = Field(
        default_factory=lambda: {
            "had_it_recently": 21,
            "too_much_prep": 60,
            "dont_fancy_it": 90,
            "wrong_for_slot": 180,
        }
    )
    # The window the *reason tally* counts over, which is deliberately longer
    # than any single dish's window above. The two halves of
    # `build_rejection_rule` are different signals: a dish name is a veto on
    # one recipe and should expire, while a run of "too much prep" answers is
    # a standing statement about how you want to eat and is the more valuable
    # half. So the tally outlives the names it was counted from.
    rejection_reason_window_days: int = Field(default=180, ge=1)


DEFAULT_PLANNING_RULES = PlanningRules().model_dump()


class DietaryRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_nova_groups: List[int] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_NOVA_GROUPS))
    banned_ingredients: List[str] = Field(default_factory=list)
    # Keys into config.json's top-level `diet_styles` catalog (see
    # `DietStyle` below) — the standing dietary approach(es) this week
    # follows, e.g. "mediterranean_diet". Empty by default, so a config
    # predating this feature generates exactly as before. Cross-checked
    # against the catalog by `AppConfig.diet_styles_are_known` rather than
    # here, because `DietaryRules` alone can't see `diet_styles`.
    active_diet_styles: List[str] = Field(default_factory=list)


class DietStyle(BaseModel):
    """One `diet_styles` catalog entry: a standing eating pattern (Mediterranean,
    Fast 800, DASH, ...) distinct from a `cuisines` entry.

    A cuisine is a flavour tradition picked per day/block
    (`pick_cuisine_blocks`); a diet style is a food-selection philosophy that
    applies across whatever cuisine is cooking that night — `principles` is
    sent to the model as guidance layered on top of the cuisine rule, never
    instead of it. See `build_diet_style_rule`.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    principles: str
    # The one numeric lever a diet style is allowed, and it is a *ceiling*
    # rather than an adjustment — see `diet_style_calorie_ceiling` for why
    # that distinction is the whole reason this can exist at all without
    # reopening the double-count `hydrate_dynamic_targets` guards against.
    # None (the default, and every style but Fast 800) means the style says
    # nothing about the day's energy and shapes food selection alone, which
    # is how all twelve behaved before this field existed.
    calorie_ceiling: Optional[float] = None


class SourcingRules(BaseModel):
    """config/schedule.json's "sourcing" object: what can actually be bought
    where this week is being cooked.

    Lives beside `regional` rather than inside `dietary_rules` because it is a
    fact about the shops, not about the body — move house and every value here
    changes while not one dietary rule does. It is the *soft* half of a
    constraint whose hard half already exists: `banned_ingredients` rejects a
    named ingredient outright at validation, but a blocklist can only name what
    somebody already thought of, and "not stocked within an hour's drive" has
    an unenumerable tail (galangal, curry leaves, yuzu, tatsoi, fresh whole
    fish). So this shapes what the model reaches for; `banned_ingredients`
    still polices what it must never return. Same division of labour as
    `diet_styles` vs. `banned_ingredients`.

    Every field defaults to "no constraint", so a checkout whose schedule.json
    predates this section generates a byte-identical prompt to before it
    existed — the same tolerance `inventory_instruction` and
    `build_diet_style_rule` extend to an empty list.
    """

    model_config = ConfigDict(extra="forbid")

    # The shops a week is actually bought from. Named in the prompt, so
    # "Coles, Woolworths or Aldi" tells the model far more about what is on
    # the shelf than any adjective would.
    supermarkets: List[str] = Field(default_factory=list)
    # Which weekday names a specialty grocer (Asian grocer, continental deli,
    # health-food shop, fishmonger, farmers' market) is actually reachable —
    # a Saturday market run, say. None means every day (no constraint, the
    # old bare `True`); [] means no day (the old bare `False`); a list
    # restricts specialty ingredients to cook days that fall on it. This is
    # the single biggest source of ingredients that read as ordinary in a
    # recipe and are unbuyable at the till on an average weeknight.
    specialty_grocers_available_days: Optional[List[str]] = None
    # Same None/[]/list-of-weekdays shape, for when a fishmonger or reliable
    # fresh counter is reachable. Seafood outside those days is steered to
    # tinned and frozen rather than dropped, since both are stocked everywhere
    # and `PORTION_DENSITY_GUARD` already leans on tinned sardines and
    # mackerel.
    fresh_seafood_available_days: Optional[List[str]] = None
    # A whole-week cap on meals whose dominant protein is seafood, counted
    # across meal types by `generate_week_plan` and spent in
    # `MEAL_TYPE_PRIORITY` order, so dinner gets first claim on it. None means
    # uncapped. See `build_seafood_limit_rule`.
    max_seafood_meals_per_week: Optional[int] = None


class StorageWindows(BaseModel):
    """`inventory_rules.storage_windows` — how long each kind of dish keeps.

    Typed loosely (`Dict[str, int]`) rather than a field per class, for the
    same reason `location_rules` is: the *shape* is open. A new storage class
    is a row in a food-safety reference table, and adding one should not be a
    schema change. `week.storage_window_for` is what knows the vocabulary, and
    it fails short on anything it does not recognise, so an unknown row here
    can only ever shorten a window rather than silently lengthen one.

    Fridge figures are **hours** and freezer figures are **months** — two
    different units because the two guidance tables are stated that way, and
    `week.storage_day_gaps` is the one place hours become the days every
    surface actually prints. Deliberately no day figure beside the hour one:
    they are different claims and a reader would take them for the same.
    """

    model_config = ConfigDict(extra="forbid")

    fridge: Dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_STORAGE_WINDOWS["fridge"])
    )
    freezer_months: Dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_STORAGE_WINDOWS["freezer_months"])
    )


class InventoryRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # There is deliberately no `fridge_safe_days` here any more. It was one
    # global number read in six places and it was wrong in both directions at
    # once — see `week.DEFAULT_STORAGE_WINDOWS`. Keeping it beside
    # `storage_windows` would leave two places to state the same thing, free
    # to disagree, which is the exact complaint this file makes about
    # `weekly_schedule`'s inert calories.
    storage_windows: StorageWindows = Field(default_factory=StorageWindows)
    perishable_day_gap: int = DEFAULT_INVENTORY_RULES["perishable_day_gap"]


class ServingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servings_per_meal: int = DEFAULT_SERVINGS_PER_MEAL


class ShoppingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_days: List[str] = Field(default_factory=list)


class UISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_scale_limit: float = 1.6
    title_tooltip_chars: int = 38


# The two macros that have both a calculated form and a hand-written one, and
# therefore need somebody to say which is live. Fat is absent because it is
# always derived (`derive_fat_g`) and `net_carbs_g` because it has no
# calculated form at all — the engine takes whatever `weekly_schedule` states
# and hands it straight back, which is what makes carbs the week's cycling
# lever. Both of those are honest answers to "where does this number come
# from"; neither is a mode anyone can switch.
TARGET_MODE_MACROS = ("calories", "protein_g")
TARGET_MODE_AUTO = "auto"
TARGET_MODE_MANUAL = "manual"


def target_is_stated(config: dict, day: str, macro: str) -> bool:
    """Whether `weekly_schedule`'s own number is the final word for this macro.

    Two independent ways to say so, deliberately different in lifetime.
    `target_modes` is the standing setting in profile.json — "protein is a
    number I choose" — and covers the whole week. `target_locks` is per-day
    and injected at runtime by `PlannerState.planning_config`, marking the
    values a review-dialog override just typed; it never reaches disk. Either
    is enough, because both mean the same thing to the arithmetic: somebody
    stated this number on purpose.

    Read by the two functions that would otherwise each decide it their own
    way — `apply_training_adjustments`, which must not grow a stated number
    by a workout's share, and `hydrate_dynamic_targets`, which must not
    replace it with a computed one. A second copy of this rule would let the
    two disagree about whose number a day is aiming at, which shows up as a
    target that moves when you toggle who owns it.
    """
    if macro in ((config.get("target_locks") or {}).get(day) or ()):
        return True
    modes = config.get("target_modes") or {}
    return (modes.get(macro) or TARGET_MODE_AUTO) == TARGET_MODE_MANUAL


class TargetModes(BaseModel):
    """Which source each switchable macro target is read from.

    `hydrate_dynamic_targets` used to overwrite every day's calories, protein
    and fat unconditionally, which made `weekly_schedule`'s stated numbers
    dead weight the moment a weigh-in existed — and, worse, silently
    discarded a deliberate override typed into the review dialog, since
    nothing downstream could tell an edited value apart from a stale file
    one. This is the missing statement of intent: `auto` means the engine
    computes it and the file's number is ignored, `manual` means the file's
    number is the target and the engine leaves it alone.

    Defaults are `auto` for both, which is exactly the behaviour before this
    existed — so a config predating it plans identically, and the flag only
    ever changes a week somebody deliberately switched.
    """

    model_config = ConfigDict(extra="forbid")

    calories: Literal["auto", "manual"] = TARGET_MODE_AUTO
    protein_g: Literal["auto", "manual"] = TARGET_MODE_AUTO


class UserProfile(BaseModel):
    """config.json's "user_profile" object: the person the week is planned for.

    Distinct from `weekly_schedule`, which holds the macro targets *chosen*
    for each day. This is the standing body-composition context those targets
    are eventually derived from — age, height and activity level are what an
    expenditure estimate needs, and `target_weight_kg`/`protein_multiplier`
    are what turn "where I'm heading" into a protein floor. It is the input
    side of the adaptive loop whose measured side is `biometrics.json` (see
    `PlanRepository.load_biometrics`), and `hydrate_dynamic_targets` is what
    joins the two: given this plus the latest weigh-in, it *replaces* every
    day's chosen calories and protein with computed ones. Leaving it unfilled
    is what keeps a week planning off `weekly_schedule` alone.

    Every field is optional with a benign default so a config.json predating
    this section still loads — the same tolerance every other section here
    extends. Age is deliberately *not* stored: `birth_date` is the fact that
    stays true, and a stored age silently rots.
    """

    model_config = ConfigDict(extra="forbid")

    # ISO YYYY-MM-DD, matching the date format biometrics.json keys on.
    birth_date: Optional[str] = None
    height_cm: Optional[float] = None
    gender: Optional[str] = None
    target_weight_kg: Optional[float] = None
    # Grams of protein per kg of body weight. 1.8 sits in the usual
    # recomposition range; it is a multiplier rather than a gram figure so it
    # keeps meaning the same thing as the weight it multiplies changes.
    protein_multiplier: float = 1.8
    # Grams of fibre below which a day is never planned, whatever its energy.
    # A flat number rather than a multiplier because fibre scales with the
    # volume of food eaten, not with the body eating it —
    # `nutrition_engine.calculate_fiber_target_g` supplies the energy half and
    # this is the floor it may only ever raise. Defaulted from the engine's own
    # constant so the number is stated once.
    fiber_floor_g: float = DEFAULT_FIBER_FLOOR_G
    activity_level: str = "light_office"


class DaySchedule(BaseModel):
    """One `weekly_schedule.<day>` entry: the day's whole-day macro target.

    **There is deliberately no `fiber_g` here**, even though the day now has a
    fibre target. Fat is stated *and* recomputed, which is a historical
    accident this section tolerates rather than repeats: a key the file may
    write and the app then ignores is a second place for a number to be wrong,
    which is the whole complaint the note at the end of CLAUDE.md makes about
    `weekly_schedule`'s inert calories. Fibre is derived from the day's final
    calorie figure by `calculate_fiber_target_g`, appears on the hydrated
    config and on `WeekPlan.targets`, and is not a file key — so `extra=forbid`
    naming the file and the line is what a hand-written one gets, rather than
    silent acceptance and silent replacement.

    `meal_overrides` stays a loose `Dict[str, Any]` — see the module-level
    note above about `meal_overrides_for` owning per-item tolerance for a
    malformed override.
    """

    model_config = ConfigDict(extra="forbid")

    calories: float
    protein_g: float
    net_carbs_g: float
    fat_g: float
    meal_overrides: Dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """The full schema of config.json, validated once at load time.

    `model_dump()` hands the rest of the app back a plain dict — every
    downstream function still takes `config: dict`, including the ones that
    thread it into `instructor`'s validation `context=`, so this is about
    validating the file up front, not about rewriting how config flows
    through the rest of the codebase. Once a dict has passed through here,
    every field this model declares is guaranteed present with a real
    (possibly defaulted) value, which is what lets call sites drop the
    `.get(key, SOME_DEFAULT)` guard they used to need.
    """

    model_config = ConfigDict(extra="forbid")

    week_start_day: str = "Monday"
    meal_types: List[str] = Field(default_factory=lambda: list(DEFAULT_MEAL_TYPES))
    user_profile: UserProfile = Field(default_factory=UserProfile)
    # Which of `weekly_schedule`'s numbers are live and which are ignored in
    # favour of the engine's — see `TargetModes` and `hydrate_dynamic_targets`.
    target_modes: TargetModes = Field(default_factory=TargetModes)
    weekly_schedule: Dict[str, DaySchedule]
    week_defaults: Dict[str, str] = Field(default_factory=dict)
    meal_styles: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    meal_weights: Dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_MEAL_WEIGHTS))
    cuisines: List[str] = Field(default_factory=list)
    # cuisine -> the cuisines that share enough of a pantry to sit next to it
    # in the week's second block (see `pick_cuisine_blocks`). Optional and
    # advisory: an unlisted cuisine just falls back to the global LRU pick, so
    # a config that never fills this in blocks exactly as before, only without
    # the shared-ingredient bonus.
    cuisine_affinities: Dict[str, List[str]] = Field(default_factory=dict)
    cuisine_meal_types: List[str] = Field(default_factory=list)
    # The pool `pick_cuisine_blocks` draws from to satisfy
    # `planning_rules.min_baseline_cuisine_share` — a subset of `cuisines`,
    # not a separate vocabulary. Empty by default, which is the feature's off
    # switch: with nothing to reserve blocks for, `pick_cuisine_blocks` picks
    # exactly as it did before this existed regardless of the share config.
    baseline_cuisines: List[str] = Field(default_factory=list)
    # The diet-style catalog (see `DietStyle`); which entries are active for
    # this week lives on `dietary_rules.active_diet_styles` instead, next to
    # the app's other standing dietary constraints.
    diet_styles: Dict[str, DietStyle] = Field(default_factory=dict)
    serving_rules: ServingRules = Field(default_factory=ServingRules)
    shopping: ShoppingConfig = Field(default_factory=ShoppingConfig)
    training_schedule: List[Dict[str, Any]] = Field(default_factory=list)
    # Two shapes, both legal: a bare string is an unquantified item, a dict is
    # `{"item": ..., "quantity_g": ...}`. Typed loosely for the same reason
    # `location_rules` is — the shape is still open, and `inventory_entries` is
    # the single reader that validates it, dropping a malformed entry with a
    # warning rather than costing a week of generation over a config typo.
    inventory_to_clear: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)
    enable_sunday_prep: bool = False
    max_prep_active_mins: int = 120
    dietary_rules: DietaryRules = Field(default_factory=DietaryRules)
    planning_rules: PlanningRules = Field(default_factory=PlanningRules)
    inventory_rules: InventoryRules = Field(default_factory=InventoryRules)
    ui_settings: UISettings = Field(default_factory=UISettings)
    # config/schedule.json's location half: where each day is spent, what that
    # implies for a meal, and the region the week is planned in. Declared here
    # so the file validates, but nothing reads them yet — see the end of
    # CLAUDE.md's "config/ is seven files". Typed loosely on purpose:
    # a schema for data no code consumes would be a guess, and `extra="forbid"`
    # means the alternative is a config file the app refuses to load.
    base_schedule: Dict[str, str] = Field(default_factory=dict)
    location_rules: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    regional: Dict[str, Any] = Field(default_factory=dict)
    # `regional`'s first actual consumer: `build_sourcing_rule` reads the two
    # together, so "Coles, Woolworths or Aldi" is qualified by "regional VIC,
    # AU" rather than left to mean whatever the model assumes.
    sourcing: SourcingRules = Field(default_factory=SourcingRules)

    @model_validator(mode="after")
    def default_cuisine_meal_types_to_meal_types(self) -> "AppConfig":
        """An empty `cuisine_meal_types` means "every meal type" — resolved
        once here rather than as `config.get("cuisine_meal_types") or
        meal_types(config)` at every call site."""
        if not self.cuisine_meal_types:
            self.cuisine_meal_types = list(self.meal_types)
        return self

    @model_validator(mode="after")
    def diet_styles_are_known(self) -> "AppConfig":
        """`dietary_rules.active_diet_styles` entries must exist in the
        `diet_styles` catalog — the same "fail at load, name the typo" policy
        `Ingredient.reject_banned_ingredients` gives banned ingredients,
        applied here instead of `DietaryRules` because only `AppConfig` can
        see both fields at once."""
        unknown = sorted(set(self.dietary_rules.active_diet_styles) - set(self.diet_styles))
        if unknown:
            raise ValueError(
                f"dietary_rules.active_diet_styles names unknown diet_styles entries: "
                f"{unknown}. Known: {sorted(self.diet_styles)}"
            )
        return self


def load_app_config(raw: dict) -> dict:
    """Validate `raw` (config.json's parsed JSON) against `AppConfig` and
    hand back a plain, fully-populated dict.

    Raising here — before `generate_week_plan` makes a single API call —
    turns a schema mistake (a typo'd key, a string where a number belongs,
    an unknown top-level section) into one clear message instead of a
    `KeyError`/`TypeError` surfacing minutes later, three functions deep into
    a run, or seven times over as every day's generation hits the same
    missing field.
    """
    try:
        app_config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"config.json failed schema validation:\n{exc}") from exc
    return app_config.model_dump(mode="json")


def planning_rule(config: Optional[dict], key: str):
    """Read one `planning_rules` value out of `config`.

    `config` may be None for a caller with no config loaded yet (e.g. a
    preview before one's been chosen) — that still resolves to
    `DEFAULT_PLANNING_RULES`. Any config that *has* been loaded went through
    `load_app_config`, so `planning_rules` is guaranteed to carry every key
    with a real value.

    A key missing from a present `planning_rules` also falls back rather than
    raising, which is what lets a rule added here reach a config dict some
    test or caller hand-built before it existed — `load_app_config` fills
    defaults in, but nothing forces a dict through it. A config with no
    `planning_rules` section *at all* falls back for the same reason and is
    the same situation one level up: `AppConfig` already carries a
    `default_factory` for the section, so its absence is legal rather than a
    misconfiguration worth raising over.
    """
    rules = DEFAULT_PLANNING_RULES if config is None else config.get("planning_rules")
    if not rules:
        return DEFAULT_PLANNING_RULES[key]
    if key not in rules:
        return DEFAULT_PLANNING_RULES[key]
    return rules[key]


# `inventory_rule(config, key)` lived here — the `planning_rule` twin for
# `inventory_rules` — and its only caller was `storage_note` reading
# `fridge_safe_days`. That key is gone (see `week.DEFAULT_STORAGE_WINDOWS`),
# and the section's one remaining key, `perishable_day_gap`, is read through
# the module constant rather than through config; that mismatch is filed in
# CHANGE-QUEUE.md with its own plan. Deleted rather than kept as a seam for
# that fix, because a reader nothing reads is the exact shape this codebase
# has now closed four times from the other direction.


# Share of a workout's estimated_burn_kcal that flows to carbs vs. protein —
# glycogen-heavy cardio skews carb, resistance work skews protein. Shares sum
# to 1 per type so the whole burn is accounted for and fat_g (derived from
# whatever calories are left) is left exactly as it was before the workout.
TRAINING_INTENSITY_SPLIT = {
    "gym_hypertrophy": {"carb_share": 0.5, "protein_share": 0.5},
    "cardio_run": {"carb_share": 0.75, "protein_share": 0.25},
    "cardio_hiit": {"carb_share": 0.65, "protein_share": 0.35},
    "cardio_ride": {"carb_share": 0.75, "protein_share": 0.25},
    "cardio_easy": {"carb_share": 0.7, "protein_share": 0.3},
    "walk": {"carb_share": 0.7, "protein_share": 0.3},
}

# Approximate clock time for each meal type. The schema has no real per-meal
# time, so this is a fixed stand-in used only to find which meal a workout
# sits closest to — good enough to decide "before" vs. "after", not a
# calendar feature.
MEAL_TIME_OF_DAY = {
    "breakfast": "07:00",
    "lunch": "12:30",
    "snack": "15:30",
    "dinner": "19:00",
}

# A workout within this many minutes *after* a meal is "fuelled by" that
# meal, per the spec's "within 2 hours" rule for digestion constraints.
TRAINING_PRE_WORKOUT_DIGESTION_MINUTES = 120

# How each `training_notes` entry opens. Constants rather than literals inline
# in `apply_training_adjustments` because the UI reads these notes back: the
# Today tab classifies a meal as the day's post- or pre-workout one by testing
# the prefix, and shows the rest of the sentence as the reason. Matching on a
# shared constant is what stops a reworded prompt from silently dropping the
# badge — the alternative is a second copy of this wording in `ui_state`,
# free to fall out of step with the note it is meant to describe.
TRAINING_NOTE_PREFIXES = {
    "post": "[POST-WORKOUT MEAL:",
    "pre": "[PRE-WORKOUT MEAL:",
}

# A session starting at or before this hour is one breakfast has to be built
# around rather than merely budgeted for — see `morning_training_days`.
MORNING_TRAINING_CUTOFF = "11:00"
# Session type prefixes that qualify, matched with `str.startswith` so a new
# `gym_strength` is covered without editing this. Hypertrophy training is
# what actually needs the fast-digesting, protein-forward refuel a shake
# gives; cardio and a `walk` both deliberately don't qualify — forcing a
# shake on every cardio morning would empty the breakfast rotation for a
# session that doesn't need this specific fuelling.
WORKOUT_BREAKFAST_TYPES = ("gym",)
# Must be a key in config.json's `meal_styles.breakfast` — `resolve_auto_choices`
# checks that before pinning, so a config without this style keeps rotating
# normally instead of briefing the model on a style it was never given.
WORKOUT_BREAKFAST_STYLE = "custom_shake"


def clock_minutes(value: str) -> int:
    hours, _, minutes = str(value).partition(":")
    try:
        return int(hours or 0) * 60 + int(minutes or 0)
    except ValueError:
        # A drawer time field is free text — a malformed value must not take
        # the whole telemetry preview down with it, same tolerance as a
        # malformed meal_override.
        logger.warning("training_schedule: ignoring unparseable time '%s'", value)
        return 0


def training_pin_budget(day_targets: dict, meal_type: str, weights: dict) -> dict:
    """The fixed budget `apply_training_adjustments` pins on a post-workout meal.

    Half the day's carbs — the glycogen the session just spent — plus the
    meal's usual weighted share of protein and fat. `split_targets` then makes
    the day's other meals absorb whatever is left, the same as for any pin
    written by hand into `meal_overrides`.

    Its own function because `hydrate_dynamic_targets` has to compute it a
    second time: the pin is a fixed number derived from the day's targets, and
    hydration replaces those targets underneath it. A pin left at its
    pre-hydration value is a meal claiming a share of a day that no longer
    exists — on a 144 g protein day it claimed 49 g worked out from the file's
    164, which was enough to push the day's snack below the protein floor and
    make `apply_protein_floor` give up on the whole day.
    """
    day_fat_g = derive_fat_g(
        day_targets["calories"], day_targets["protein_g"], day_targets["net_carbs_g"]
    )
    weight = weights.get(meal_type, 0.25) or 0.25
    pinned_protein = round(day_targets["protein_g"] * weight, 1)
    pinned_carbs = round(day_targets["net_carbs_g"] * 0.5, 1)
    pinned_fat = round(day_fat_g * weight, 1)
    return {
        "calories": round(pinned_protein * 4 + pinned_carbs * 4 + pinned_fat * 9, 1),
        "protein_g": pinned_protein,
        "net_carbs_g": pinned_carbs,
        "fat_g": pinned_fat,
    }


def apply_training_adjustments(config: dict) -> dict:
    """Fold `config["training_schedule"]` into targets before they're calculated.

    Returns a new config — this module never mutates the one it's handed —
    with three changes per scheduled (non-rest) session:

    A. Daily budget expansion: `estimated_burn_kcal` is added straight onto
       the day's `calories`, and split into `protein_g`/`net_carbs_g`
       additions by `TRAINING_INTENSITY_SPLIT`. The shares sum to 1, so
       `derive_fat_g` lands on the same fat_g the day had before the workout
       — a workout buys back carbs and protein, not fat.
    B. Meal slot pinning: whichever configured meal type sits closest in
       clock time (`MEAL_TIME_OF_DAY`) to the workout, on the side it
       follows, is given a `meal_override` carrying half the day's
       (post-expansion) carbs, its usual weighted share of protein and fat.
       `meal_overrides_for`'s two-pass split then takes care of the rest —
       the other meals absorb what's left exactly as any other pin does. An
       explicit override the config already set for that meal always wins;
       this never overwrites one.
    C. Digestion rules: any meal within `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES`
       *before* the workout gets a prompt note (`training_notes`, read by
       `build_slot_brief`) asking for low-fibre, low-fat, easily digestible
       food — a constraint on what the meal is made of, not its macros, so it
       doesn't fight step B.

    Called once, up front (CLI: `run_cli`; UI: `PlannerState.planning_config`)
    so every downstream reader — `week_targets`, `meal_overrides_for`,
    `build_slot_brief` — sees the same already-adjusted config rather than
    each needing its own patch.

    Step A's per-day arithmetic is also recorded verbatim under
    `training_uplift`, because `hydrate_dynamic_targets` runs *after* this in
    both entry points and overwrites the very numbers A added to. Recording the
    delta is what lets it put the burn back without re-deriving the split from
    `TRAINING_INTENSITY_SPLIT` a second time, which would leave two copies of
    these rules to keep in agreement. All three keys are recorded even though
    hydration replays only `calories` — the record is what A *did*, and which
    parts of it survive a locked protein target is that function's decision to
    document, not this one's to pre-empt.
    """
    sessions = [
        session
        for session in config["training_schedule"]
        if session.get("type") != "rest" and float(session.get("estimated_burn_kcal", 0) or 0) > 0
    ]
    if not sessions:
        return config

    schedule = {day: dict(targets) for day, targets in config["weekly_schedule"].items()}
    notes: Dict[str, Dict[str, str]] = {}
    uplift: Dict[str, Dict[str, float]] = {}
    pins: Dict[str, List[str]] = {}
    weights = config["meal_weights"]
    day_meals = [meal_type for meal_type in meal_types(config) if meal_type in MEAL_TIME_OF_DAY]

    for session in sessions:
        day = session.get("day")
        if day not in schedule:
            logger.warning("training_schedule: ignoring session for unknown day '%s'", day)
            continue
        split = TRAINING_INTENSITY_SPLIT.get(session.get("type"))
        if split is None:
            logger.warning(
                "training_schedule: ignoring session with unknown type '%s' on %s",
                session.get("type"), day,
            )
            continue

        burn = float(session["estimated_burn_kcal"])
        added = {
            "calories": burn,
            "protein_g": burn * split["protein_share"] / 4,
            "net_carbs_g": burn * split["carb_share"] / 4,
        }
        day_targets = schedule[day]
        day_uplift = uplift.setdefault(day, {})
        for key, amount in added.items():
            # A stated target is the day's final number, so a workout does
            # not grow it — see `target_is_stated`. Recording the uplift it
            # didn't apply would be worse than not recording it: hydration
            # replays this record onto the engine's base, and the review
            # dialog draws it as the amber segment of the day's bar, so a
            # figure nothing added would show up as calories the day does
            # not have.
            if target_is_stated(config, day, key):
                continue
            day_targets[key] = day_targets.get(key, 0) + amount
            # Accumulated, not assigned: two sessions on one day each expand it.
            day_uplift[key] = day_uplift.get(key, 0.0) + amount

        if not day_meals:
            continue
        workout_minutes = clock_minutes(session.get("time", "00:00"))
        nearest = min(
            day_meals, key=lambda meal: abs(clock_minutes(MEAL_TIME_OF_DAY[meal]) - workout_minutes)
        )

        if clock_minutes(MEAL_TIME_OF_DAY[nearest]) >= workout_minutes:
            overrides = dict(day_targets.get("meal_overrides") or {})
            if nearest not in overrides:
                overrides[nearest] = training_pin_budget(day_targets, nearest, weights)
                day_targets["meal_overrides"] = overrides
                # Recorded so `hydrate_dynamic_targets` can tell this pin (a
                # number this function derived from targets it is about to
                # replace) from one written by hand into config.json, which is
                # a deliberate fixed budget and must survive untouched.
                pins.setdefault(day, []).append(nearest)
                notes.setdefault(day, {})[nearest] = (
                    f"{TRAINING_NOTE_PREFIXES['post']} high glycogen "
                    "replenishment required — carb-forward to refuel after "
                    f"{humanize(session.get('type'))}]"
                )

        for meal in day_meals:
            gap = workout_minutes - clock_minutes(MEAL_TIME_OF_DAY[meal])
            # A meal at the exact workout minute (gap 0) already has the
            # post-workout note from the pin above if it was the nearest one
            # — setdefault leaves that in place rather than overwriting it.
            if 0 <= gap <= TRAINING_PRE_WORKOUT_DIGESTION_MINUTES:
                notes.setdefault(day, {}).setdefault(
                    meal,
                    f"{TRAINING_NOTE_PREFIXES['pre']} low-fibre, ultra-easily "
                    "digestible, low-fat fuel — a "
                    f"{humanize(session.get('type'))} session follows at "
                    f"{session.get('time')}]",
                )

    adjusted = dict(config, weekly_schedule=schedule)
    if notes:
        adjusted["training_notes"] = notes
    if uplift:
        adjusted["training_uplift"] = uplift
    if pins:
        adjusted["training_pins"] = pins
    return adjusted


def is_free_model(model: str) -> bool:
    return model.endswith(":free")


def reasoning_extra_body(model: str, config: dict) -> dict:
    """OpenRouter's `extra_body` for turning a model's hidden reasoning off.

    Disabled by default — see CLAUDE.md "Reasoning must be disabled": the
    identical prompt shape measured 303s and, on repeated runs, zero content
    (finish_reason "length") with it left on. Some providers go further and
    reject the request outright whenever the key is present at all (a hard
    400 "Reasoning is mandatory for this endpoint and cannot be disabled",
    not a retryable validation failure — `google/gemini-3.6-flash` did this
    on every call of a real run, failing the whole week in under a second).
    `models.json` marks such a model `"reasoning_required": true` in its
    `models` table; for them the key is omitted entirely rather than sent as
    `enabled: True` — the reason this task disables reasoning in the first
    place (no deliberation needed, the macro arithmetic is already done in
    Python) doesn't change just because the model insists on doing it anyway.

    The flag lives on the model's own entry rather than in a second parallel
    list of ids, because a list beside the selectable ones is free to name a
    model that is no longer offered, or to miss one that is.
    """
    if model_metadata(config, model).get("reasoning_required"):
        return {}
    return {"reasoning": {"enabled": False}}


def meal_type_week_max_tokens(model: str, num_recipes: int) -> int:
    """Token budget for a MealTypeWeekRecipes call, scaled to how many
    recipes it's actually asking for in this one request.

    FREE/PAID_MODEL_MAX_TOKENS were sized for generate_day's calls, which
    never asked for more than 4 recipes (one day's meal types). A meal-type
    call can ask for up to 7 (every day of the week) — a real run measured
    a 7-dinner call using 14900 of a flat 16000-token budget, 93% of it, one
    verbose day away from `finish_reason: length`, which returns *zero*
    content (see CLAUDE.md "Reasoning must be disabled"), not a merely
    truncated response. Scaling per recipe, at the same per-recipe rate the
    flat constants implied for a 4-recipe day, fixes that; the `max(...)`
    floor keeps a small call (e.g. a single regenerated meal) exactly as
    generous as before.
    """
    base = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS
    per_recipe = base // 4
    return max(base, per_recipe * num_recipes)


def derive_fat_g(calories: float, protein_g: float, net_carbs_g: float) -> float:
    """Fat is whatever energy is left once protein and carbs are paid for.

    The one place this arithmetic lives, so a per-meal override and a whole-day
    target are derived by the identical rule.
    """
    return max(0, (calories - (protein_g * 4 + net_carbs_g * 4)) / 9)


def fiber_floor_g(config: dict) -> float:
    """`user_profile.fiber_floor_g`, or the engine's default.

    One reader rather than two `.get` chains, for the reason the note at the
    end of CLAUDE.md gives about `planned_targets`: a number the UI displays
    and a number a run plans against must come from one call. An unfilled
    `user_profile` — the state a fresh checkout is in — still has a fibre
    floor, because unlike a calorie target it needs nothing about the body to
    be known.
    """
    profile = config.get("user_profile") or {}
    floor = profile.get("fiber_floor_g")
    return DEFAULT_FIBER_FLOOR_G if floor is None else float(floor)


def calculate_daily_targets(day_of_week: str, config: dict) -> dict:
    """One day's whole-day macro target, with fat derived rather than read.

    The entry point for the rule this whole app is built on: Python computes
    every number the model is later *told*, so the model only ever fills in
    food. `fat_g` is deliberately recomputed from calories/protein/carbs via
    `derive_fat_g` even when `weekly_schedule` states one — so a config whose
    four numbers don't balance can't hand the model an impossible budget, and
    so a low `net_carbs_g` day automatically becomes a high-fat day with no
    separate keto flag.

    Raises on an unknown day rather than defaulting: a typo'd weekday in
    config.json must fail here, before any API call, not silently plan a week
    against zeros.
    """
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
    # Derived from the day's calories the same way fat is, and by the same
    # function `hydrate_dynamic_targets` uses, so the file path and the engine
    # path cannot disagree about a Thursday. `fiber_g` on the day is ignored if
    # stated, exactly as `fat_g` is.
    fiber_g = calculate_fiber_target_g(calories, fiber_floor_g(config))

    return {
        "day_of_week": day_of_week,
        "calories": calories,
        "protein_g": protein_g,
        "net_carbs_g": net_carbs_g,
        "fat_g": round(fat_g, 1),
        "fiber_g": round(fiber_g, 1),
    }


def week_targets(spec: WeekSpec, config: dict) -> Dict[str, dict]:
    return {day: calculate_daily_targets(day, config) for day in spec.days}


def with_fiber_targets(config: dict, floor_g: Optional[float] = None) -> dict:
    """`config` with every `weekly_schedule` day carrying its fibre target.

    The fallback half of `hydrate_dynamic_targets`, split out because that
    function has **four** ways to hand back a schedule and only one of them
    runs the engine. Every fibre figure is derived from the day's own
    calories, so the three no-engine paths can produce the identical number
    without one — and they have to, or `/api/targets` (which reads the
    hydrated `weekly_schedule` straight out) would report fibre on a machine
    with a weigh-in and omit it on one without, while the telemetry header
    read `calculate_daily_targets` and printed a figure either way. That is
    the "one number, one call" rule at the end of CLAUDE.md, reached from the
    storage side.
    """
    schedule = config.get("weekly_schedule") or {}
    if not schedule:
        return config
    floor = fiber_floor_g(config) if floor_g is None else floor_g
    return dict(
        config,
        weekly_schedule={
            day: dict(
                entry,
                fiber_g=round(
                    calculate_fiber_target_g(
                        float(entry.get("calories") or 0.0), floor
                    ),
                    1,
                ),
            )
            for day, entry in schedule.items()
        },
    )


def hydrate_dynamic_targets(
    config: dict,
    latest_biometrics: Optional[dict],
    note_callback=None,
    biometrics: Optional[dict] = None,
    log: bool = True,
) -> dict:
    """Recompute every `weekly_schedule` day from the body, not the file.

    `config.json`'s per-day calories and protein are numbers somebody typed
    once. `nutrition_engine.calculate_macro_targets` derives them instead from
    `user_profile` plus the most recent weigh-in: BMR (Katch-McArdle when the
    scale reported body fat, Mifflin-St Jeor otherwise), TDEE from the activity
    factor, and a deficit that slides with the remaining gap to
    `target_weight_kg`. Returns a new config — this module never mutates the
    one it's handed — with each day's `calories`, `protein_g`, `fat_g` and
    `fiber_g` replaced and everything else about the day left alone.

    **`fiber_g` is derived, never owned, which is why it has no
    `target_modes` entry.** `TARGET_MODE_MACROS` exists for the two macros
    with two possible sources; fibre, like fat, has exactly one
    (`nutrition_engine.calculate_fiber_target_g` of the day's final calorie
    figure), so a toggle for it would be a control that changes nothing.

    **TDEE comes from measurement once there is enough of it.** Given the full
    `biometrics` file (not just the latest weigh-in),
    `nutrition_engine.calculate_adaptive_tdee` infers expenditure from what was
    actually logged and what the scale actually did — mean intake plus the
    least-squares weight trend x 7700 kcal/kg. That measures the one body in
    question rather than predicting it from a population regression that can
    sit 300 kcal out on an individual. `reconcile_adaptive_tdee` bounds it
    against the formula (`ADAPTIVE_TDEE_TOLERANCE`) and keeps the formula when
    the two disagree wildly, because under-logging depresses the measured
    figure and a fortnight of water weight can dominate the trend;
    `basis["tdee_source"]` records which won. Omit `biometrics` — as the pure
    function's existing callers do — and it is the plain formula, unchanged.

    **An active diet style may cap the result, and only cap it.**
    `diet_style_calorie_ceiling` reads the lowest `calorie_ceiling` any entry
    in `dietary_rules.active_diet_styles` declares — Fast 800's 800 kcal is
    the one that ships — and the computed figure is taken `min()` against it,
    after the training uplift is replayed. After, because a ceiling is a
    statement about what the day may *total*, not about the base it was built
    from; and `min()` rather than a subtraction because this function runs
    twice on the same config and only an idempotent operation survives that
    (see the stated-target note below, which is the same lesson). A day whose
    calories are stated — either mode — is not capped at all: a stated target
    is the day's final number by definition, and a ceiling that overrode one
    would be the second source of truth this whole section refuses.

    Three things it deliberately preserves rather than computes:

    - **Each day's `net_carbs_g`.** It is passed *into* the engine rather than
      overwritten, so carb cycling written into `weekly_schedule` survives and
      only the energy and protein become dynamic. Fat then derives from
      whatever's left, which is what makes a low-carb day a high-fat one with
      no keto flag — the same rule `calculate_daily_targets` already applies.
    - **`meal_overrides`.** A pinned meal is a fixed budget by definition,
      including the one `apply_training_adjustments` pins post-workout.
    - **A training day's expanded energy**, replayed from `training_uplift`.
      `apply_training_adjustments` runs first in both entry points, so without
      this a workout's burn would be silently overwritten by the un-expanded
      dynamic figure.

    Only the *calorie* uplift is replayed, and the other two keys are skipped
    for different reasons:

    - `net_carbs_g` is read out of `weekly_schedule` and passed straight into
      the engine, which returns it verbatim — so the day's carb figure already
      carries the workout's carb share. Replaying it would double it.
    - `protein_g` is not replayed because protein is *locked*, below. A
      workout's energy buys back carbs (its share is already in the figure
      above) and fat, not protein.

    **Protein is locked to the target weight, not today's and not the day's
    activity** — 80 kg x 1.8 is 144 g whether the scale says 100 or 84 and
    whether or not there's a session that evening, because the point of the
    protein is to hold the lean mass being carried toward that target. It is
    therefore the same number every day of the week, where the file had it
    drifting between 110 and 120 by day and a training day pushed it to 188.

    **Which numbers it is allowed to replace is a setting, not a given.**
    `config["target_modes"]` (profile.json, see `TargetModes`) marks a
    switchable macro `auto` or `manual` for the whole week, and
    `config["target_locks"]` — injected per-day at runtime by
    `PlannerState.planning_config`, never written to disk — marks the values
    a review-dialog override just typed. A macro that is manual by either
    route is taken verbatim off `weekly_schedule` and the engine is not
    consulted for it. Before this existed the overwrite was unconditional,
    which made every override of calories or protein a silent no-op: the UI
    accepted the number, the bar moved, and generation planned the computed
    figure anyway.

    `log=False` suppresses the summary and fallback log lines. The UI's
    `planning_config()` hydrates on *every* repaint to keep the telemetry
    header honest, and a per-keystroke "dynamic targets: ..." line would
    bury the per-call generation timing this log exists for.

    Falls back to the file's numbers, with a warning, when the engine can't
    compute — no weigh-in and no `current_weight_kg`, or a Mifflin profile
    with no `birth_date`. That is not the "substitute a plausible body" the
    engine refuses to do: `weekly_schedule` holds real targets somebody chose,
    so falling back plans a deliberately-configured week rather than a
    fabricated one. `biometrics.json` is empty until the first Garmin sync
    lands, so this is the normal path on a fresh checkout, not an edge case.
    """
    profile = config.get("user_profile") or {}
    if not any(profile.get(key) for key in ("target_weight_kg", "height_cm", "birth_date")):
        # An all-defaults UserProfile (every field None) means the section
        # isn't filled in, not that it's absent — nothing to hydrate from.
        # Fibre still resolves: it needs the day's calories and a floor, and
        # neither is a fact about the body.
        return with_fiber_targets(config)

    uplift = config.get("training_uplift") or {}
    pins = config.get("training_pins") or {}
    floor_g = fiber_floor_g(config)
    weights = config.get("meal_weights") or DEFAULT_MEAL_WEIGHTS
    # None whenever no active diet style declares one, which is the shipped
    # state — `active_diet_styles` is empty by default, so every day below
    # is byte-identical to before this existed.
    ceiling = diet_style_calorie_ceiling(config)
    capped_days: List[str] = []
    # Days where the ceiling lands below what the locked protein and the
    # day's own carbs already cost. `derive_fat_g` floors at 0, so the budget
    # stops reconciling against `calories ~= 4p + 4c + 9f` — reported rather
    # than silently corrected, the same answer `split_targets` gives an
    # overspent `meal_overrides` and `cap_to_weighted_share` gives a capped
    # surplus: show the gap, do not distort a number to hide it.
    unaffordable_days: List[str] = []

    # Computed once for the week, not per day: it reads the whole weigh-in and
    # logged-intake series, which every day of this config shares. Returns None
    # whenever the data can't support an estimate — too few weigh-ins, too
    # short a span, nothing logged — which is the normal state until a few
    # weeks of sync have accumulated, and reads through as the plain formula.
    adaptive_tdee = None
    if biometrics:
        adaptive_tdee = calculate_adaptive_tdee(
            biometrics.get("daily_actuals") or [],
            biometrics.get("weigh_ins") or [],
        )

    schedule: Dict[str, dict] = {}
    modes = config.get("target_modes") or {}

    def is_manual(day: str, macro: str) -> bool:
        return target_is_stated(config, day, macro)

    # The engine is consulted only for macros something actually reads off
    # it. A week with every switchable macro manual is planned entirely from
    # `weekly_schedule`, and computing a BMR to then discard is what would
    # make a checkout with no weigh-in warn about targets nobody is using.
    needs_engine = any(
        not is_manual(day, macro)
        for day in config["weekly_schedule"]
        for macro in TARGET_MODE_MACROS
    )

    # BMR, TDEE, the adaptive reconciliation, the deficit ramp and the locked
    # protein figure depend on the body and the adaptive estimate, never on
    # the day — only net_carbs_g (and the fat_g it derives) varies per day.
    # Computed once rather than once per weekday so `basis` is a single value
    # rather than whatever the loop's last iteration happened to leave behind.
    base = None
    basis = None
    if needs_engine:
        try:
            base = calculate_macro_targets(
                profile, latest_biometrics, adaptive_tdee=adaptive_tdee
            )
        except (ValueError, TypeError) as exc:
            message = short_error(exc)
            if log:
                logger.warning(
                    "dynamic targets unavailable, using config.json targets — %s", message
                )
            if note_callback:
                note_callback(f"Using config.json targets — {message}")
            return with_fiber_targets(config, floor_g)
        basis = base["basis"]
    try:
        for day, day_targets in config["weekly_schedule"].items():
            # A stated figure is taken exactly as it stands.
            # `apply_training_adjustments` has already declined to grow it by
            # the workout's share (`target_is_stated`), so there is nothing
            # to unwind here — which is also what makes this idempotent, and
            # it has to be: the UI hydrates once for its own live preview and
            # generation hydrates that same config again. Subtracting an
            # uplift instead was correct exactly once and wrong on the second
            # pass, taking a 2200 kcal override down to 1850.
            #
            # The `auto` branch replays the uplift onto the engine's base,
            # because there the figure it was added to has just been thrown
            # away.
            if is_manual(day, "calories"):
                calories = float(day_targets["calories"])
            else:
                calories = base["calories"] + (uplift.get(day) or {}).get("calories", 0.0)
                # After the uplift, not before it: the ceiling bounds what the
                # day may total, and a workout does not buy an exemption from
                # a bound its owner chose to eat inside.
                if ceiling is not None and calories > ceiling:
                    capped_days.append(day)
                    calories = ceiling
            if is_manual(day, "protein_g"):
                protein_g = float(day_targets["protein_g"])
            else:
                protein_g = base["protein_g"]
            day_carbs = day_targets.get("net_carbs_g")
            net_carbs_g = round(
                DEFAULT_NET_CARBS_G if day_carbs is None else day_carbs, 1
            )
            if ceiling is not None and protein_g * 4 + net_carbs_g * 4 > calories:
                unaffordable_days.append(day)
            hydrated = dict(
                day_targets,
                calories=round(calories),
                protein_g=round(protein_g, 1),
                net_carbs_g=round(net_carbs_g, 1),
                fat_g=round(derive_fat_g(calories, protein_g, net_carbs_g), 1),
                # Derived from the day's *final* calorie figure — after the
                # uplift and after the diet-style ceiling — for the same
                # reason fat is: both are answers about the day that was
                # settled, not about the base it was built from. It has no
                # `target_modes` entry and cannot get one, because like fat
                # it has only ever had one possible source; `is_manual`
                # governs where the *calories* came from, and fibre follows
                # whatever they turned out to be.
                fiber_g=round(
                    calculate_fiber_target_g(calories, floor_g), 1
                ),
            )
            # The post-workout pin was worked out from the numbers just
            # replaced, so it has to be worked out again from the new ones —
            # otherwise it claims a share of a day that no longer exists and
            # drags the day's remaining meals below the protein floor. Only
            # pins this run's `apply_training_adjustments` wrote are touched;
            # a hand-written `meal_overrides` entry is a deliberate fixed
            # budget and is left exactly as config.json states it.
            for meal_type in pins.get(day) or []:
                overrides = dict(hydrated.get("meal_overrides") or {})
                overrides[meal_type] = training_pin_budget(hydrated, meal_type, weights)
                hydrated["meal_overrides"] = overrides
            schedule[day] = hydrated
    except (ValueError, TypeError) as exc:
        # One message, not one per day: every day fails identically here,
        # because they differ only in the carb figure the failure never
        # reaches. Same reasoning as checking the API key up front.
        message = short_error(exc)
        if log:
            logger.warning(
                "dynamic targets unavailable, using config.json targets — %s", message
            )
        if note_callback:
            note_callback(f"Using config.json targets — {message}")
        return with_fiber_targets(config, floor_g)

    if not schedule:
        # An empty `weekly_schedule` never enters the loop, so nothing raises
        # into the fallback above — but there is no day to read a protein
        # figure off, which the log line below would dereference via
        # `next(iter(schedule))`. Nothing to hydrate is the file's own state,
        # not a failure, so hand back what we were given.
        return config

    if capped_days:
        # One line naming the days, not one line per day: the ceiling is a
        # single standing decision and seven copies of it would bury the
        # per-call generation timing this log exists for.
        if log:
            logger.info(
                "diet-style calorie ceiling of %.0f kcal applied to %s",
                ceiling, ", ".join(capped_days),
            )
        if note_callback:
            note_callback(
                f"Capped {len(capped_days)} day(s) at the {ceiling:.0f} kcal "
                "diet-style ceiling"
            )
    if unaffordable_days:
        # Worth a warning rather than a silent 0 g of fat: protein is locked
        # to the *target* weight and carbs come straight off
        # `weekly_schedule`, so a ceiling below their combined cost is a
        # config disagreement the user has to resolve — by raising the
        # ceiling, or by cutting that day's `net_carbs_g`.
        if log:
            logger.warning(
                "the %.0f kcal diet-style ceiling is below what locked protein and "
                "carbs already cost on %s — fat floors at 0 and those days will not "
                "reconcile against 4p + 4c + 9f. Raise the ceiling or lower "
                "weekly_schedule's net_carbs_g.",
                ceiling, ", ".join(unaffordable_days),
            )
        if note_callback:
            note_callback(
                f"{len(unaffordable_days)} day(s) cannot fit locked protein and "
                f"carbs under the {ceiling:.0f} kcal ceiling"
            )

    if basis is None:
        # Every switchable macro is manual, so nothing was computed and there
        # is no basis to report. The week is still hydrated — fat is derived
        # and the training pins were rebuilt — it just came entirely from
        # `weekly_schedule`, which is what the setting asked for.
        if log:
            logger.info(
                "targets from config.json: every switchable macro is manual "
                "(%s kcal/day, %.0fg protein)",
                sorted({entry["calories"] for entry in schedule.values()}),
                schedule[next(iter(schedule))]["protein_g"],
            )
        if note_callback:
            note_callback("Targets read from config.json — calories and protein set to manual")
        return dict(config, weekly_schedule=schedule)

    if log:
        logger.info(
            "dynamic targets: %s kcal/day base, %.0fg protein (BMR %.0f by %s, TDEE %.0f "
            "from %s, deficit %.0f, weight %.1fkg -> %.1fkg)",
            sorted({entry["calories"] for entry in schedule.values()}),
            schedule[next(iter(schedule))]["protein_g"],
            basis["bmr"], basis["bmr_method"], basis["tdee"], basis["tdee_source"],
            basis["deficit_kcal"], basis["current_weight_kg"], basis["target_weight_kg"],
        )
    if log and basis["tdee_source"] == "formula_adaptive_rejected":
        logger.warning(
            "measured TDEE of %.0f kcal is more than %.0f%% from the formula's %.0f — "
            "ignoring it and planning on the formula. Usually under-logged intake or "
            "a weigh-in series dominated by water weight.",
            basis["tdee_adaptive"],
            ADAPTIVE_TDEE_TOLERANCE * 100,
            basis["tdee_formula"],
        )
    if note_callback:
        source = {
            "adaptive": "measured from intake + weight trend",
            "formula_adaptive_rejected": "formula; measured figure looked wrong",
        }.get(basis["tdee_source"], "formula")
        # Named per macro rather than as one blanket "targets from
        # biometrics". Once a macro can be manual, that sentence is a claim
        # about where a number came from, and the run's own note is the worst
        # place for it to be wrong — a manual protein figure reported as
        # "locked at 144g (80kg x 1.8)" describes arithmetic that did not
        # happen. `modes` alone here, not `is_manual`: a per-day review
        # override is a handful of days, where the mode is the whole week.
        manual = [
            macro for macro in TARGET_MODE_MACROS
            if (modes.get(macro) or TARGET_MODE_AUTO) == TARGET_MODE_MANUAL
        ]
        parts = [
            f"TDEE {basis['tdee']:.0f} kcal ({source}) - {basis['deficit_kcal']:.0f} deficit"
            if "calories" not in manual
            else "calories manual, from config.json"
        ]
        if "protein_g" in manual:
            parts.append(
                f"protein manual at "
                f"{schedule[next(iter(schedule))]['protein_g']:.0f}g"
            )
        else:
            parts.append(
                f"protein locked at {schedule[next(iter(schedule))]['protein_g']:.0f}g "
                f"({basis['target_weight_kg']:.0f}kg x {profile.get('protein_multiplier') or 1.8})"
            )
        note_callback("Targets: " + ", ".join(parts))
    # `dynamic_basis` is diagnostic only — nothing plans off it. It rides on
    # the config so a log line or a future UI readout can say *why* the week
    # is aiming where it is, which two runs a fortnight apart will disagree on.
    return dict(config, weekly_schedule=schedule, dynamic_basis=basis)


async def hydrate_config(
    config: dict, repository: Optional[PlanRepository] = None, note_callback=None
) -> dict:
    """`hydrate_dynamic_targets` with the latest weigh-in fetched for it.

    The async half is only the storage read, kept apart so the arithmetic
    stays a pure function a test can call with a literal weigh-in dict.

    Called at the top of each of the three generation entry points
    (`generate_week_plan`, `regenerate_single_day`, `regenerate_single_meal`)
    rather than once in the CLI, because the NiceGUI front end builds its
    config in `PlannerState.planning_config()` — a synchronous method that
    cannot await storage. Hydrating where the repository is already in hand
    means both front ends generate against the same numbers with no UI change,
    and a regenerated meal aims at the same day target as the run that
    produced its siblings.
    """
    store = repository or LocalJSONRepository()
    # Two reads rather than one, deliberately. "Latest" is a question about
    # dates, not list order (see `get_latest_biometrics`), and reimplementing
    # that rule here to save a read of a small JSON file would be a second
    # place for it to be wrong. `load_biometrics` supplies the *series* the
    # adaptive estimate needs, which is a different question.
    latest = await store.get_latest_biometrics()
    biometrics = await store.load_biometrics()
    return hydrate_dynamic_targets(config, latest, note_callback, biometrics)


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
    raw = config["weekly_schedule"].get(day, {}).get("meal_overrides") or {}
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


def recent_main_proteins(history: List[dict], config: Optional[dict] = None) -> List[str]:
    """Main proteins across the last few days, de-duplicated, so the model can
    be told not to repeat them."""
    lookback_entries = planning_rule(config, "protein_lookback_entries")
    seen = set()
    proteins = []
    for entry in history[-lookback_entries:]:
        for protein in entry.get("main_proteins", []):
            if protein not in seen:
                seen.add(protein)
                proteins.append(protein)
    return proteins


def recent_recipe_names(history: List[dict]) -> List[str]:
    """Recipe names across the whole retained history, de-duplicated, so an
    exact dish is never regenerated within the non-repeat window.

    Unlike `recent_main_proteins`, this is not sliced to
    `protein_lookback_entries` — it walks every entry `record_week_history`
    kept, which is exactly `history_max_entries` (28, a 4-week window). A
    protein just needs to *rotate*; a recipe name is meant not to repeat at
    all inside that window.
    """
    seen = set()
    names = []
    for entry in history:
        for name in entry.get("recipe_names", []):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def cuisine_block_sizes(num_days: int, pattern: Optional[List[int]] = None) -> List[int]:
    """`pattern` scaled to however many days of this meal type are cooked.

    The pattern is a *ratio*, not a day count: config.json states 4/3 for a
    full week of dinners, but a week where three of them are last night's
    leftovers only has four cooks to lay out. Apportioned largest-remainder so
    the sizes always sum to `num_days` exactly, and zero-size blocks drop out
    — below one day per block the week is simply one block, which is the
    correct answer rather than a degenerate case to guard against.
    """
    sizes_pattern = [int(size) for size in (pattern or DEFAULT_CUISINE_BLOCK_PATTERN) if size > 0]
    if num_days <= 0 or not sizes_pattern:
        return []
    total = sum(sizes_pattern)
    exact = [num_days * size / total for size in sizes_pattern]
    sizes = [int(value) for value in exact]
    # Largest fractional part first, ties to the earlier (larger) block, so a
    # 4/3 pattern over 5 days puts the extra day in the 4-block.
    order = sorted(range(len(sizes)), key=lambda index: (-(exact[index] - sizes[index]), index))
    for index in order[: num_days - sum(sizes)]:
        sizes[index] += 1
    return [size for size in sizes if size > 0]


def _reserve_baseline_block_indices(sizes: List[int], min_share: float, num_days: int) -> set:
    """Which block indices (into `sizes`) must be drawn from the baseline pool
    to clear `min_share` of `num_days`.

    Largest blocks first, so the fewest blocks possible are reserved — that
    leaves the most room for the rest of the week to rotate through the other
    cuisines freely rather than being squeezed toward the floor as well.
    Ties keep the earlier block, matching `cuisine_block_sizes`' own
    tie-break, so which block is "first" stays predictable.
    """
    if min_share <= 0 or num_days <= 0:
        return set()
    target = math.ceil(min_share * num_days)
    order = sorted(range(len(sizes)), key=lambda index: (-sizes[index], index))
    reserved: set = set()
    total = 0
    for index in order:
        if total >= target:
            break
        reserved.add(index)
        total += sizes[index]
    return reserved


def pick_cuisine_blocks(
    num_days: int,
    cuisines: List[str],
    recent: List[str],
    affinities: Optional[Dict[str, List[str]]] = None,
    pattern: Optional[List[int]] = None,
    baseline_cuisines: Optional[List[str]] = None,
    min_baseline_share: float = 0.0,
) -> List[str]:
    """One cuisine per cooked day, laid out in contiguous blocks.

    Returns a flat list of length `num_days` — the first block's cuisine
    repeated for its days, then the second's — so the caller zips it straight
    against the days in week order.

    A per-slot `next_choice` can only ever produce a different country every
    night, which is what this replaces: seven cuisines is seven half-used jars
    of paste, seven bunches of a herb used once, and a shopping list with no
    overlap anywhere in it. Blocking is the food-waste lever, not a stylistic
    one.

    The first block is the strict-LRU pick `next_choice` would have made
    anyway, so rotation across weeks is unchanged. Each later block prefers a
    cuisine `config.cuisine_affinities` lists as complementary to the one
    before it (LRU among those), falling back to the global LRU pick when that
    list is empty or already spent. Complementary is what makes the split pay
    at the till: thai then vietnamese share fish sauce, lime, coriander and
    rice noodles, where thai then cajun buys two of everything and finishes
    neither.

    `baseline_cuisines`/`min_baseline_share` add a floor on top of that:
    enough of the week's largest blocks are reserved to a `baseline_cuisines`
    cuisine to clear the share, and only those blocks have their pool
    narrowed — everything else about the pick (affinity-first, LRU fallback,
    strict rotation) runs unchanged, just against the narrower pool for a
    reserved block. A block that already prefers a baseline cuisine via
    affinity (mexican -> homestyle, say) still picks it through the normal
    affinity path; the floor only forces the issue when affinity and LRU
    alone wouldn't have gotten there. No `baseline_cuisines` (the feature's
    off switch) reserves nothing and this is exactly the old function.
    """
    sizes = cuisine_block_sizes(num_days, pattern)
    if not cuisines or not sizes:
        return []

    affinities = affinities or {}
    baseline = [cuisine for cuisine in (baseline_cuisines or []) if cuisine in cuisines]
    reserved = _reserve_baseline_block_indices(sizes, min_baseline_share, num_days) if baseline else set()

    seen = list(recent)
    chosen: List[str] = []
    for index, _ in enumerate(sizes):
        pool = baseline if index in reserved else cuisines
        options: List[str] = []
        if index and chosen:
            options = [
                cuisine
                for cuisine in affinities.get(chosen[-1], [])
                if cuisine in pool and cuisine not in chosen
            ]
        if not options:
            # `or list(pool)` covers a pattern with more blocks than the pool
            # has cuisines — repeating one is better than a block with no
            # cuisine at all, which would leave those days unthemed.
            options = [cuisine for cuisine in pool if cuisine not in chosen] or list(pool)
        cuisine = next_choice(options, seen)
        chosen.append(cuisine)
        seen.append(cuisine)

    return [cuisine for cuisine, size in zip(chosen, sizes) for _ in range(size)]


def morning_training_days(config: dict) -> List[str]:
    """Days carrying a morning gym (hypertrophy) session early enough to train on.

    `resolve_auto_choices` pins these days' breakfast to
    `WORKOUT_BREAKFAST_STYLE`, unconditionally — this is a hard rule, not a
    suggestion the model can weigh against anything else. A shake is the
    only breakfast in `meal_styles` that can be drunk in the ten minutes
    before a session and still be digested by the first set; left to the
    style rotation, a 06:30 gym slot gets eggs and smoked salmon on toast
    roughly one week in five, and the rotation has no way to know why that
    is wrong.

    Cardio and a walk don't count, and neither does anything after
    `MORNING_TRAINING_CUTOFF`. Cardio's fuelling need isn't the same as a
    hypertrophy session's — forcing a shake on every cardio morning would
    empty the breakfast rotation for a session that doesn't need this
    specific refuel. An evening session is already handled, as macros rather
    than as a menu: `apply_training_adjustments` expands the day's budget,
    pins the meal after it, and marks the meal before it as pre-workout
    fuel. Pinning a *style* is only warranted when the session lands before
    the meal has any time to settle.
    """
    cutoff = clock_minutes(MORNING_TRAINING_CUTOFF)
    days: List[str] = []
    for session in config.get("training_schedule") or []:
        if not str(session.get("type") or "").startswith(WORKOUT_BREAKFAST_TYPES):
            continue
        if clock_minutes(session.get("time") or "00:00") > cutoff:
            continue
        day = session.get("day")
        if day and day not in days:
            days.append(day)
    return days


def recipe_last_scheduled(history: List[dict]) -> Dict[str, date]:
    """recipe name -> the most recent date it was planned for.

    Only entries carrying a real `date` count. `record_week_history` writes
    None for a plan generated before `WeekPlan.week_start_date` existed, and
    an undated entry cannot answer "how long ago" — treating it as "never
    scheduled" is the safe reading, since the alternative is silently
    re-serving a favourite that was on last week's plan.

    **This is bounded by `planning_rules.history_max_entries`**, which is a cap
    on entries (one per cooked day), not on days. A favourite that fell off
    the end of that window is indistinguishable here from one never cooked, so
    the retention window has to stay comfortably longer than the longest reuse
    window in `favorite_reuse_days` or the rule silently stops binding.
    """
    latest: Dict[str, date] = {}
    for entry in history:
        raw = entry.get("date")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw)[:10]).date()
        except ValueError:
            continue
        for name in entry.get("recipe_names", []):
            if name not in latest or when > latest[name]:
                latest[name] = when
    return latest


def eligible_favorites(
    favorites: List[dict],
    meal_type: str,
    reuse_days: int,
    last_scheduled: Dict[str, date],
    today: date,
    already_used: Set[str],
) -> List[dict]:
    """Favourites of `meal_type` not cooked within `reuse_days`, oldest first.

    Ordered by how long ago each was last scheduled, never-scheduled ones
    first — the same least-recently-used rule `next_choice` applies to styles
    and cuisines, for the same reason: "unused in the last N" starves the tail
    of a short list, and a favourites list is by nature short.

    `meal_type` is read off the saved recipe itself, which every catalog
    record carries (`Recipe.meal_type` is a required field), so a dinner can
    never be picked for a breakfast slot.
    """
    candidates = []
    for record in favorites:
        recipe = record.get("recipe") or {}
        name = recipe.get("name")
        if not name or recipe.get("meal_type") != meal_type or name in already_used:
            continue
        when = last_scheduled.get(name)
        if when is not None and (today - when).days < reuse_days:
            continue
        candidates.append((when or date.min, record))
    return [record for _, record in sorted(candidates, key=lambda pair: pair[0])]


def favorite_fits_day(record: dict, day: str, config: Optional[dict] = None) -> bool:
    """False for a `long_oven_cook` favourite on a day with no hours in it.

    A *generated* dinner is asked to put the week's one long cook on a day
    that suits it (`build_batch_roast_rule`), but a favourite never passes
    through that prompt: `select_favorite_assignments` places dinners at
    cuisine run ends, which is a rule about protecting blocks and says nothing
    about the day having the hours in it. Nothing downstream caught it either
    — `prep_limit_for`'s 30-minute weeknight ceiling counts *active* minutes,
    and a braise honestly reports the 20 that are hands-on rather than the
    8-10 hours it then sits in the oven, so it clears the cap it ought to
    fail. That is how a "Slow Cooked Beef Cheeks" imported from Keep came to
    be cooked on a Thursday. Eight of the 36 dinner favourites in the shipped
    catalog are long cooks, so this is a recurring shape rather than one bad
    record.

    **Which days those are is `day_allows_long_cook`, not the weekend.** This
    rule used to key on the calendar, with its own docstring recording that
    widening it to the days you are actually home was "a real improvement, and
    belongs here when it happens" — the worry being a second, subtler notion
    of "a day with room to cook" that would have to stay in agreement with
    `prep_limit_for` and the batch-roast rule. It is not a second notion: it
    is the *other axis*. Active minutes are a claim on your attention and stay
    weeknight-versus-weekend; elapsed hours are a claim on your presence, and
    that is what a braise needs and what `base_schedule` already knows. The
    generated path now reads the identical function, so there is one answer
    rather than two.

    Reads the catalog record's raw dict rather than a validated `Recipe`
    because selection runs before anything is validated — `generate_week_plan`
    skips a record too broken to load, and until then an absent flag reads as
    False exactly as `Recipe.long_oven_cook`'s own default does.
    """
    if not (record.get("recipe") or {}).get("long_oven_cook"):
        return True
    return day_allows_long_cook(day, config)


def favorite_keeps_long_enough(
    record: dict, slot_id_value: str, config: Optional[dict] = None
) -> bool:
    """False for a favourite that cannot keep as long as its slot needs.

    The sibling of `favorite_fits_day`, and a second axis rather than a second
    reading of the same one: that function asks whether the *day* has the
    hours to cook the dish, this asks whether the *dish* survives to the last
    meal that eats it. A slow-cooked stew passes one and a rice tray bake
    fails the other, and neither implies the other.

    It exists because a pinned favourite is a **third route to a long span**,
    beside a batch spread and a hand-built leftover chain, and it is the one
    route no prompt or response validator can see: a favourite is never
    generated, so `build_storage_rule` never briefs it and
    `reject_short_storage_class` never judges it. Without this the two halves
    would disagree in the mirror image of the bug `favorite_fits_day` was
    written for — there a saved braise was refused a Thursday that a generated
    braise could take; here a generated rice dish would be refused a span a
    saved one could quietly claim.

    Reads the raw catalog dict for the same reason `favorite_fits_day` does:
    selection runs before anything is validated, and an absent
    `storage_class` reads as None — which is the shortest window, not the
    default one. Every recipe in the shipped catalog predates this field, so
    that is the common case and not an edge one: an unclassified favourite can
    take a slot eaten within two days of its cook, and anything longer goes to
    generation, where the model is asked what the dish actually is.
    """
    spans = (config or {}).get("storage_spans") or {}
    needed = spans.get(slot_id_value, 0)
    if needed <= 0:
        return True
    storage_class = (record.get("recipe") or {}).get("storage_class")
    return fridge_day_gaps(storage_class, config) >= needed


def cuisine_run_ends(slots: List[SlotSpec]) -> List[SlotSpec]:
    """The last slot of each contiguous same-cuisine run, in week order.

    `pick_cuisine_blocks` lays cuisines down in contiguous blocks (4/3 by
    default) and `pin_recipe` blanks the cuisine of any slot a favourite
    claims, so *which* day a pin takes decides how much of a block survives
    it. Taking a run's last day leaves the remainder contiguous; taking a
    middle day splits one block into two shorter ones with a hole between
    them, which is the arrangement `build_cuisine_continuity_rule` then has
    to describe and the shopping list pays for.

    Slots with no cuisine each count as their own run — a week that never
    resolved its cuisines (nothing pinned, or a caller that skipped
    `resolve_auto_choices`) degrades to plain earliest-first order, which is
    what lunch already does.
    """
    ends: List[SlotSpec] = []
    for position, slot in enumerate(slots):
        following = slots[position + 1] if position + 1 < len(slots) else None
        if following is None or not slot.cuisine or following.cuisine != slot.cuisine:
            ends.append(slot)
    return ends


def select_favorite_assignments(
    spec: WeekSpec,
    config: dict,
    history: List[dict],
    favorites: List[dict],
    today: Optional[date] = None,
) -> Dict[str, dict]:
    """slot_id -> catalog record, for the slots a saved favourite should fill.

    The stage between "what kind of meal goes here" (`resolve_auto_choices`)
    and "invent one" (`generate_meal_type_week`): some slots don't need
    inventing, because there is already a dish you know you want. Every slot
    this returns is one fewer recipe the model is asked for, so it also makes
    a week cheaper to generate.

    The rules, in the order they are applied:

    - **Breakfast.** A morning gym session's breakfast is already pinned to a
      shake by `resolve_auto_choices` and is skipped here — that pin is a hard
      nutritional rule and a favourite must not displace it. Whatever
      breakfasts remain get one favourite spread across up to
      `favorite_breakfast_slots` days, rather than a different favourite each
      morning: the point of a standing breakfast is that it is the same one,
      and it also means a single shop covers both mornings.
    - **Lunch.** One favourite per eligible slot, drawn from the same LRU
      order. Office lunches are handled by the grid rather than here — they
      default to the previous day's dinner (`week.apply_location_modes`), so
      by the time this runs an Office lunch is usually a leftover and not a
      cook slot at all. The ones that reach here are the days you actually
      cook lunch.
    - **Dinner.** Up to `favorite_dinner_slots` (2) *distinct* favourites —
      a count of dishes, not of days one dish covers, and counted as pins
      made rather than run ends looked at, because dinner is where
      repetition shows and `DINNER_VARIETY_RULE` says so out loud. Capped
      rather than lunch's one-per-slot because dinner is the only meal type
      `pick_cuisine_blocks` lays contiguous blocks over and `pin_recipe`
      blanks the cuisine of every slot it claims: uncapped against a large
      catalog there would be no block left and the pantry overlap would go
      with it.

      **Placement is one per cuisine run, taking the last day of each**, and
      that is the whole reason this reads `slot.cuisine` at all. Blocks are
      already resolved by the time this runs (`resolve_auto_choices` precedes
      it), so the runs are visible — and blanking a run's *last* day leaves
      the rest of it contiguous, where blanking a middle day would split one
      block into two shorter ones with a hole between them. Taking one per
      run rather than two from the same run also spreads the favourites
      across the week instead of clustering them, and damages each block
      equally rather than halving one and leaving the other whole. A week
      with no cuisines resolved falls back to earliest-first, same as lunch.
    - **Snack.** Nothing, deliberately. `week_defaults.snack` is `skip` in the
      shipped config (see "The floor and the day have to be affordable
      together"), so there is usually no snack slot to claim — and a rule
      whose slots do not exist is one that cannot be seen to be wrong.

    Cutting across all three: a favourite marked `long_oven_cook` is only
    ever offered a day with the hours in it (`favorite_fits_day`, which
    explains why, and `day_allows_long_cook`, which decides which days those
    are). Elsewhere it is passed over and the next eligible favourite takes
    that day instead — so the day still gets a dish, and the long cook waits
    for a day with the hours in it rather than being dropped for the week.
    Breakfast is the one case with no per-day choice left to make, since a
    single record covers both claimed mornings, so it has to suit both.

    A slot the user has already pinned by hand keeps its pin — the same
    precedence `pin_style` gives a hand-picked style, checked here by only
    ever filling a slot whose `recipe_id` is empty.
    """
    today = today or date.today()
    reuse_days = planning_rule(config, "favorite_reuse_days")
    breakfast_slots = planning_rule(config, "favorite_breakfast_slots")
    dinner_slots = planning_rule(config, "favorite_dinner_slots")
    last_scheduled = recipe_last_scheduled(history)

    assignments: Dict[str, dict] = {}
    # Names already on the grid this week — a favourite must not be served
    # twice in one week by two different rules, and a hand-pinned slot counts.
    used: Set[str] = set()

    def open_slots(meal_type: str) -> List[SlotSpec]:
        return [
            slot
            for slot in spec.cook_slots()
            if slot.meal_type == meal_type
            and not slot.recipe_id
            # The workout shake is a hard rule, not a preference.
            and not (meal_type == "breakfast" and slot.style == WORKOUT_BREAKFAST_STYLE)
        ]

    breakfasts = sorted(open_slots("breakfast"), key=lambda slot: spec.day_index(slot.day))
    if breakfasts and breakfast_slots > 0:
        # One record covers every claimed morning, so it has to suit all of
        # them — unlike lunch and dinner below, there is no per-day choice
        # left to make once the pick is made.
        claimed = breakfasts[:breakfast_slots]
        pick = [
            record
            for record in eligible_favorites(
                favorites, "breakfast", reuse_days.get("breakfast", 7),
                last_scheduled, today, used,
            )
            if all(
                favorite_fits_day(record, slot.day, config)
                and favorite_keeps_long_enough(record, slot.id, config)
                for slot in claimed
            )
        ]
        if pick:
            record = pick[0]
            used.add(record["recipe"]["name"])
            for slot in claimed:
                assignments[slot.id] = record

    lunches = sorted(open_slots("lunch"), key=lambda slot: spec.day_index(slot.day))
    for slot in lunches:
        pick = eligible_favorites(
            favorites, "lunch", reuse_days.get("lunch", 21), last_scheduled, today, used
        )
        if not pick:
            break
        fits = [
            record
            for record in pick
            if favorite_fits_day(record, slot.day, config)
            and favorite_keeps_long_enough(record, slot.id, config)
        ]
        # `continue`, not `break`: nothing eligible suits *this* day, but a
        # later slot may be a weekend one that it does suit. Only an empty
        # `pick` means the favourites are actually spent.
        if not fits:
            continue
        record = fits[0]
        used.add(record["recipe"]["name"])
        assignments[slot.id] = record

    dinners = sorted(open_slots("dinner"), key=lambda slot: spec.day_index(slot.day))
    # The cap counts pins *made*, not run ends considered, so a run end passed
    # over by `favorite_fits_day` doesn't quietly cost the week a favourite —
    # it moves the pin to the next run end, which is still a run end and still
    # leaves its block contiguous.
    pinned = 0
    for slot in cuisine_run_ends(dinners):
        if pinned >= dinner_slots:
            break
        pick = eligible_favorites(
            favorites, "dinner", reuse_days.get("dinner", 21), last_scheduled, today, used
        )
        if not pick:
            break
        fits = [
            record
            for record in pick
            if favorite_fits_day(record, slot.day, config)
            and favorite_keeps_long_enough(record, slot.id, config)
        ]
        if not fits:
            continue
        record = fits[0]
        used.add(record["recipe"]["name"])
        assignments[slot.id] = record
        pinned += 1

    return assignments


def resolve_auto_choices(spec: WeekSpec, config: dict, history: List[dict]) -> WeekSpec:
    """Fill in every `auto` style and cuisine with a concrete choice.

    Runs before any API call so the entire week is deterministic and
    previewable: rotation continues from meal_history.json and then keeps
    rotating *within* the week, so seven auto breakfasts don't all resolve to
    whatever happens to be first in the config list.

    Two of the three choices made here are not per-slot picks, and can't be:

    - **A morning session's breakfast** is pinned to a shake before any
      rotation runs (`morning_training_days` decides which days,
      `week.pin_style` applies it), so the pinned style then seeds the LRU
      like any other and the remaining breakfasts rotate around it.
    - **Cuisines are laid out in blocks across the whole week**
      (`pick_cuisine_blocks`) rather than picked a slot at a time, because a
      block spans days and a slot-at-a-time LRU pick structurally cannot
      produce one. Explicitly chosen cuisines are left alone and seeded into
      the LRU first, so a hand-picked Wednesday pushes the auto blocks away
      from it rather than being overwritten by them.
    """
    cuisines = config["cuisines"]
    cuisine_meal_types = config["cuisine_meal_types"]
    affinities = config.get("cuisine_affinities") or {}
    block_pattern = planning_rule(config, "cuisine_block_pattern")
    baseline_cuisines = config.get("baseline_cuisines") or []
    min_baseline_share = planning_rule(config, "min_baseline_cuisine_share")

    if WORKOUT_BREAKFAST_STYLE in styles_for(config, "breakfast"):
        spec = pin_style(
            spec, "breakfast", WORKOUT_BREAKFAST_STYLE, morning_training_days(config)
        )

    recent_cuisines = history_values(history, "cuisine")
    recent_styles = {
        meal_type: history_styles(history, meal_type) for meal_type in meal_types(config)
    }

    # Seeded before the blocks are picked so an explicit choice reads as the
    # most recent use of that cuisine, which is what makes LRU steer the auto
    # blocks away from repeating it.
    recent_cuisines.extend(slot.cuisine for slot in spec.cook_slots() if slot.cuisine)

    block_cuisines: Dict[str, str] = {}
    for meal_type in cuisine_meal_types:
        auto_slots = sorted(
            (
                slot
                for slot in spec.cook_slots()
                if slot.meal_type == meal_type and not slot.cuisine
            ),
            key=lambda slot: spec.day_index(slot.day),
        )
        assigned = pick_cuisine_blocks(
            len(auto_slots),
            cuisines,
            recent_cuisines,
            affinities,
            block_pattern,
            baseline_cuisines,
            min_baseline_share,
        )
        block_cuisines.update(
            {slot.id: cuisine for slot, cuisine in zip(auto_slots, assigned)}
        )
        recent_cuisines.extend(assigned)

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

        resolved.append(
            slot.model_copy(
                update={"style": style, "cuisine": slot.cuisine or block_cuisines.get(slot.id)}
            )
        )

    return spec.model_copy(update={"slots": resolved})


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def _dietary_rule(info: ValidationInfo, key: str, default):
    """One `dietary_rules` entry out of instructor's validation context.

    Both `Ingredient` validators below read live config through
    `info.context["config"]` rather than a baked-in list, and both have to
    tolerate a caller that supplied no config at all — a bare
    `Recipe.model_validate` of a saved favorite must stay loadable rather than
    blow up.

    Shared because the obvious spelling of that tolerance is subtly wrong.
    `if info.context and "config" in info.context` tests that the *key* is
    present and then subscripts it, so a context carrying `{"config": None}`
    passes the guard and raises `TypeError` from inside Pydantic — while
    `reject_untrimmable_macro_miss`, two classes down, reads the same context
    via `planning_rule(context.get("config"))` and tolerates exactly that.
    Two conventions for one thing, one of which crashes. This is the tolerant
    one: absent, None, or missing the section all fall through to `default`.
    """
    config = (info.context or {}).get("config") or {}
    rules = config.get("dietary_rules") or {}
    value = rules.get(key)
    return default if value is None else value


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
    fiber_g: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Dietary fibre in grams. Reported for tracking only — it is not "
            "part of any macro budget, so never trade it off against the "
            "calorie, protein, carb or fat targets. Note this is fibre in "
            "addition to net_carbs_g, which already excludes it. Defaults to "
            "0.0 so recipes saved before this field existed stay loadable."
        ),
    )

    @field_validator("nova_group")
    @classmethod
    def enforce_allowed_nova_group(cls, v: int, info: ValidationInfo) -> int:
        """Reject ultra-processed ingredients at parse time, against live config.

        `info.context["config"]` is how the *current* dietary rules reach a
        Pydantic validator: it's handed to instructor's
        `client.chat.completions.create(context=...)`, so this reads
        config.json rather than a baked-in list. The fallback covers callers
        with no context — a bare `Recipe.model_validate` of a saved favorite,
        for instance — which must stay loadable rather than blow up.

        Raising here is load-bearing, not defensive: instructor catches it and
        hands the model back its own rejected output to retry, which is what
        makes the dietary rules self-correcting rather than merely checked.
        """
        allowed = _dietary_rule(info, "allowed_nova_groups", DEFAULT_ALLOWED_NOVA_GROUPS)
        if v not in allowed:
            raise ValueError(
                f"nova_group {v} is not allowed (allowed groups: {allowed}); "
                "ultra-processed (Group 4) ingredients are rejected"
            )
        return v

    @field_validator("name")
    @classmethod
    def reject_banned_ingredients(cls, v: str, info: ValidationInfo) -> str:
        """Substring blocklist over ingredient names, from live config.

        Same `info.context` mechanism as `enforce_allowed_nova_group` above.
        Matching is deliberately substring, not whole-word: the list holds
        things like "seed oils" and "hydrogenated oil", and a model writes them
        into longer names ("partially hydrogenated soybean oil") far more often
        than it writes them bare. False positives are the accepted cost — the
        rejection message names the matched term, so a bad entry is obvious.
        """
        banned = _dietary_rule(info, "banned_ingredients", [])
        name_lower = v.lower()
        for banned_item in banned:
            if banned_item.lower() in name_lower:
                raise ValueError(
                    f"ingredient '{v}' contains banned ingredient '{banned_item}'"
                )
        return v

    def per_serving_macros(self, total_servings: int = 1) -> Dict[str, float]:
        servings = max(1, total_servings)
        return {key: getattr(self, key) / servings for key in NUTRIENT_KEYS}

    def scaled(self, factor: float) -> "Ingredient":
        """Multiply quantity and macros by `factor`, quantity snapped to a
        practical grocery amount via `round_ingredient_quantity`.

        `NUTRIENT_KEYS`, not `MACRO_KEYS`: fibre is not budgeted but it is
        still linear in quantity, so halving a portion has to halve its fibre
        too. Leaving it out here would let the trim silently inflate the
        reported figure relative to the food actually on the plate.
        """
        return self.model_copy(
            update=dict(
                {key: round(getattr(self, key) * factor, 1) for key in NUTRIENT_KEYS},
                quantity_g=round_ingredient_quantity(
                    self.name, self.quantity_g * factor, categorize_department(self.name)
                ),
            )
        )


class Recipe(BaseModel):
    name: str = Field(..., description="Recipe name")
    meal_type: str = Field(..., description="breakfast, lunch, dinner, or snack")
    ingredients: List[Ingredient]
    instructions: List[str] = Field(..., description="Ordered preparation steps")
    prep_time_minutes: int = Field(..., ge=0)
    total_time_minutes: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Total wall-clock time from starting the dish to eating it, in "
            "minutes, INCLUDING unattended time — oven, slow cooker, "
            "simmering, marinating, proving, chilling. Always >= "
            "prep_time_minutes, which counts only the hands-on minutes. A "
            "braise needing 25 minutes of active work and 4 hours in the "
            "oven is prep_time_minutes 25 and total_time_minutes 265. For a "
            "dish with no unattended time the two are equal. Defaults to "
            "None (older saved recipes predate this field and their elapsed "
            "time is unknown rather than zero)."
        ),
    )
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
    long_oven_cook: bool = Field(
        default=False,
        description=(
            "True only if this dish is primarily a long (roughly 60+ minutes), "
            "mostly hands-off oven roast/bake or slow-cooker/braise — the kind of "
            "thing you start and walk away from, suited to unattended batch "
            "cooking. False for anything needing active stovetop attention, a "
            "quick recipe, or a no-cook dish, even one made in bulk. Defaults to "
            "False (older saved recipes predate this field and are conservatively "
            "treated as not hands-off)."
        ),
    )
    bulk_prep_friendly: bool = Field(
        default=False,
        description=(
            "True if this dish is well suited to big-batch cooking and "
            "multi-day storage — a soup, stew, curry, chilli, casserole or "
            "similar that reheats well. Does NOT need to be a long or "
            "hands-off cook to qualify (that's long_oven_cook, a separate "
            "field — a dish can be either, both, or neither). False for "
            "anything better made fresh in single portions. Defaults to "
            "False (older saved recipes predate this field)."
        ),
    )
    storage_class: Optional[str] = Field(
        default=None,
        description=(
            "What kind of dish this is for the purpose of how long it keeps, "
            "which is a food-safety question and not a preference. Exactly "
            f"one of: {', '.join(STORAGE_CLASSES)}. Use rice_or_pasta for any "
            "dish built on cooked rice, pasta, noodles, couscous or other "
            "cooked grains, however it is served — those keep half as long as "
            "everything else. Use default for an ordinary cooked dish that "
            "fits none of the other categories. Defaults to None, which means "
            "nobody classified it and is NOT the same answer as 'default': an "
            "unclassified dish is treated as the shortest-keeping kind, so "
            "omitting this field costs the dish storage life rather than "
            "quietly granting it."
        ),
    )


    @field_validator("prep_time_minutes")
    @classmethod
    def enforce_prep_limit(cls, v: int, info: ValidationInfo) -> int:
        """Cap active prep time at the ceiling for the day being generated.

        Only fires on the `DayRecipes` path, which is one call for one day and
        can therefore put that day in `info.context["day"]`. The
        `MealTypeWeekRecipes` path spans up to 7 days in a single call, so no
        single `day` is meaningful there — it runs its own model_validator over
        `self.recipes`' day keys instead. Both read `prep_limit_for`, which is
        also what `build_slot_brief` states in the prompt: the model is told the
        exact rule it is then judged against.

        Raising is load-bearing, not defensive — instructor catches it and hands
        the model its own output back to retry.
        """
        day = info.context.get("day") if info.context else None
        if day and v > prep_limit_for(day):
            raise ValueError(
                f"prep_time_minutes {v} exceeds the {prep_limit_for(day)}-minute limit "
                f"for {day}; simplify the recipe to fit."
            )
        return v

    @property
    def total_macros(self) -> Dict[str, float]:
        """Every `NUTRIENT_KEYS` figure summed over the ingredients.

        Carries `fiber_g` alongside the budgeted four. Every budget-side
        consumer indexes the keys it wants (`for key in MACRO_KEYS`) rather
        than iterating whatever it is handed, so the extra key reaches the
        display paths — cards, the detail dialog, the PDF — without reaching
        any arithmetic. See `NUTRIENT_KEYS`.
        """
        totals = {key: 0.0 for key in NUTRIENT_KEYS}
        for ingredient in self.ingredients:
            for key in NUTRIENT_KEYS:
                totals[key] += getattr(ingredient, key)
        return totals

    @property
    def per_serving_macros(self) -> Dict[str, float]:
        servings = max(1, self.servings)
        return {key: value / servings for key, value in self.total_macros.items()}

    def resize_by_factor(self, factor: float) -> "Recipe":
        """Multiply every ingredient's quantity and macros by `factor`.

        `servings` is left untouched — this is the single-serving portion
        trim (`fit_recipe_to_budget`), not a change in how many servings the
        recipe yields. `scale_to_servings` is the one that changes `servings`.
        """
        return self.model_copy(
            update={"ingredients": [ingredient.scaled(factor) for ingredient in self.ingredients]}
        )

    def round_ingredient_quantities(self) -> "Recipe":
        """Snap every ingredient's quantity to a practical grocery amount.

        `resize_by_factor` already does this via `Ingredient.scaled()` — this
        covers the untrimmed path, where `fit_recipe_to_budget` leaves a
        recipe's raw model-generated quantities untouched (within its
        deadband, or when there's no budget to trim to).
        """
        return self.model_copy(
            update={
                "ingredients": [
                    ingredient.model_copy(
                        update={
                            "quantity_g": round_ingredient_quantity(
                                ingredient.name,
                                ingredient.quantity_g,
                                categorize_department(ingredient.name),
                            )
                        }
                    )
                    for ingredient in self.ingredients
                ]
            }
        )

    def scale_to_servings(
        self,
        target_servings: int,
        keeps_for_days: int = 0,
        config: Optional[dict] = None,
    ) -> "Recipe":
        """Rescale from `self.servings` to `target_servings` and refresh storage notes.

        The factor is relative to `self.servings`, not assumed to be 1, so
        this covers both the model's single-serving output growing into a
        batch and an already-scaled batch being resized again after a grid
        edit changes how many slots claim it.
        """
        factor = target_servings / max(1, self.servings)
        scaled = self.resize_by_factor(factor) if factor != 1.0 else self

        prep_notes = scaled.prep_notes
        if not prep_notes or prep_notes.startswith(STORAGE_NOTE_PREFIX):
            prep_notes = (
                storage_note(
                    target_servings,
                    keeps_for_days,
                    config,
                    storage_class=self.storage_class,
                )
                or None
            )

        return scaled.model_copy(update={"servings": target_servings, "prep_notes": prep_notes})


class DayRecipes(BaseModel):
    """The model's response for a single day: one recipe per cook slot."""

    recipes: List[Recipe]

    @model_validator(mode="after")
    def enforce_long_cook_day(self, info: ValidationInfo) -> "DayRecipes":
        """A long cook may only land on a day with the hours in it.

        Reads the single `day` out of instructor's context, exactly as
        `Recipe.enforce_prep_limit` does and for the same reason: this axis is
        one call for one day. `MealTypeWeekRecipes` spans up to seven and runs
        the same check over its own day keys. Both call
        `planner.reject_misplaced_long_cook`, which is also what
        `build_long_cook_day_rule` states in the prompt, so the model is told
        the rule it is then judged against. The shared function is named apart
        from these two methods deliberately: a bare same-named call inside a
        method body resolves to the global and reads like recursion.
        """
        context = info.context or {}
        day = context.get("day")
        if day:
            reject_misplaced_long_cook(
                ((day, recipe) for recipe in self.recipes), context.get("config")
            )
        return self

    @model_validator(mode="after")
    def enforce_storage_class(self, info: ValidationInfo) -> "DayRecipes":
        """A dish must keep as long as its slot needs it to.

        Same two-axis split as `enforce_long_cook_day` directly above, over
        the same kind of context, and the same shared function
        (`planner.reject_short_storage_class`) as the `MealTypeWeekRecipes`
        copy — so the two axes cannot disagree about a Tuesday. The prompt
        half is `build_storage_rule`, which reads the same `storage_spans`
        this is judged against.
        """
        context = info.context or {}
        day = context.get("day")
        if day:
            reject_short_storage_class(
                ((day, recipe) for recipe in self.recipes), context.get("config")
            )
        return self

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "DayRecipes":
        """Bounce a response too far off budget for the portion trim to rescue.

        The threshold is derived from planning_rules.portion_trim_limits rather than picked:
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
        context = info.context or {}
        budget = context.get("day_budget")
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
        low, high = planning_rule(context.get("config"), "portion_trim_limits")
        if not low <= factor <= high:
            raise ValueError(
                f"the recipes total {total:.0f} kcal per serving but the budget for "
                f"these meals is {target:.0f} kcal ({(total - target) / target:+.0%}). "
                "Resize the portions to match each meal's stated budget — do not "
                "add or remove meals, and remember any meals already listed as "
                "fixed leftovers are NOT yours to generate."
            )
        return self


class MealTypeWeekRecipes(BaseModel):
    """The model's response for one meal type across every day it's cooked
    this week — the transposed twin of DayRecipes, which held one day's
    several meal types. See generate_week_plan for why generation is now
    organised this way (macro cascading, protein variety across dinners).

    Keyed by day name rather than a list, same reasoning as DayRecipes being
    keyed by meal_type: a dict makes a missing or misnamed day a structural
    mismatch instructor can retry on, rather than a positional guess.
    """

    recipes: Dict[str, Recipe]

    @model_validator(mode="after")
    def enforce_prep_limit(self) -> "MealTypeWeekRecipes":
        """Recipe.enforce_prep_limit reads a single `day` out of instructor's
        context — that fits DayRecipes' one-call-one-day shape but not this
        one, where a single call spans up to 7 days each needing their own
        check. Done here instead, over self.recipes' own day keys, rather than
        threading a per-item context through Pydantic. Same `prep_limit_for`
        either way, so the two paths cannot disagree.
        """
        for day, recipe in self.recipes.items():
            if recipe.prep_time_minutes > prep_limit_for(day):
                raise ValueError(
                    f"{day}: prep_time_minutes {recipe.prep_time_minutes} exceeds the "
                    f"{prep_limit_for(day)}-minute limit; simplify the recipe to fit."
                )
        return self

    @model_validator(mode="after")
    def enforce_long_cook_day(self, info: ValidationInfo) -> "MealTypeWeekRecipes":
        """Per-day version of DayRecipes.enforce_long_cook_day (see that
        docstring). Over `self.recipes`' own day keys, since a single call here
        spans up to seven days each needing their own answer — the same shape
        as `enforce_prep_limit` directly above, and the same shared function
        underneath, so the two axes cannot disagree about a Tuesday.
        """
        reject_misplaced_long_cook(
            self.recipes.items(), (info.context or {}).get("config")
        )
        return self

    @model_validator(mode="after")
    def enforce_storage_class(self, info: ValidationInfo) -> "MealTypeWeekRecipes":
        """Per-day version of DayRecipes.enforce_storage_class (see that
        docstring). Over `self.recipes`' own day keys, since a single call
        here spans up to seven days each needing their own answer — the same
        shape as `enforce_long_cook_day` directly above, and the same shared
        function underneath.

        This is the axis that matters most for storage: a whole meal type's
        week is exactly where the batch anchors live, and their spans are the
        long ones.
        """
        reject_short_storage_class(
            self.recipes.items(), (info.context or {}).get("config")
        )
        return self

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "MealTypeWeekRecipes":
        """Per-day version of DayRecipes.reject_untrimmable_macro_miss (see
        that docstring for why the threshold is derived from
        planning_rules.portion_trim_limits rather than a flat tolerance).

        Checked per day rather than pooled across the week: a week with one
        day at +80% and another at -80% would net to zero on a pooled total
        and let two rejectable days hide behind the average.
        """
        context = info.context or {}
        day_budgets = context.get("day_budgets")
        if not day_budgets:
            return self

        low, high = planning_rule(context.get("config"), "portion_trim_limits")
        problems = []
        for day, recipe in self.recipes.items():
            budget = day_budgets.get(day)
            if not budget:
                continue
            target = budget.get("calories", 0)
            total = recipe.total_macros["calories"]
            if target <= 0 or total <= 0:
                continue
            factor = target / total
            if not low <= factor <= high:
                problems.append(
                    f"{day}: {total:.0f} kcal per serving vs a budget of {target:.0f} kcal "
                    f"({(total - target) / target:+.0%})"
                )
        if problems:
            raise ValueError(
                "These days' recipes are too far off their per-serving budget for portion "
                "resizing to fix: " + "; ".join(problems) + ". Resize the portions to match "
                "each day's stated budget — do not add or remove meals."
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


class PrepPhase(BaseModel):
    """One step in the Sunday batch-prep timeline, run in order."""

    name: str
    description: Optional[str] = None
    active_minutes: int = 0
    passive_minutes: int = 0


class SundayPrepSession(BaseModel):
    """Optional Sunday batch-prep plan: raw prep work aggregated across the
    week's cook events (e.g. "dice all onions" once instead of per cook day),
    done ahead of time rather than repeated on each cook day.

    `total_active_minutes` is hands-on prep time, not the passive minutes
    spent simmering/roasting/chilling while unattended. It is validated
    against config's `max_prep_active_mins` via instructor's `context=` (see
    `enforce_active_minutes_cap`) rather than a fixed `le=` bound: the bound
    used to be hardcoded at 120 *and* the config value clamped to 120 at the
    call site, so raising `max_prep_active_mins` to 150 appeared to work and
    silently did nothing.
    """

    total_active_minutes: int
    total_passive_minutes: int = 0
    aggregated_ingredients: Dict[str, str] = Field(default_factory=dict)
    timeline: List[PrepPhase] = Field(default_factory=list)
    meals_included: List[str] = Field(
        default_factory=list,
        description="Names of the dishes this prep session covers",
    )
    candidate_slot_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Internal — not part of the model's response. "
            "generate_sunday_prep_session overwrites this with the slot_ids "
            "it actually folded in, so is_sunday_prepped can match an event "
            "against the session by slot_id rather than by trusting the "
            "recipe's own self-reported long_oven_cook/bulk_prep_friendly "
            "flags, which the model doesn't always set even when directed "
            "to mark the anchor. Empty on a session saved before this field "
            "existed — is_sunday_prepped falls back to the flag check then."
        ),
    )

    @model_validator(mode="after")
    def enforce_active_minutes_cap(self, info: ValidationInfo) -> "SundayPrepSession":
        """Reject a session that overruns the configured hands-on budget.

        Reads the cap out of instructor's `context=` so config.json is the
        single authority, the same way `Ingredient`'s validators read live
        dietary rules. Absent context (a bare `model_validate` of a saved
        plan) skips the check — an already-stored session must stay loadable
        even if the config has since been tightened.
        """
        cap = (info.context or {}).get("max_prep_active_mins")
        if cap is not None and self.total_active_minutes > cap:
            raise ValueError(
                f"total_active_minutes {self.total_active_minutes} exceeds the "
                f"{cap}-minute hands-on budget; move unattended oven/slow-cooker "
                "time into passive_minutes or drop a dish from this session."
            )
        return self


class WeekPlan(BaseModel):
    days: List[str]
    servings_per_meal: int
    generated_at: str
    week_start_date: Optional[str] = Field(
        default=None,
        description=(
            "ISO date of days[0] at the moment this plan was first generated "
            "(week.week_date_range's own anchor, computed once and kept — "
            "not recomputed on a later regenerate_single_day/meal, since "
            "those bump generated_at to the regen moment and recomputing "
            "from that would silently shift the week). None on a plan "
            "generated before this field existed; callers fall back to "
            "week.week_date_range(days, generated_at) for those, the same "
            "tolerance week_banner already extends to a missing week_start_date."
        ),
    )
    cook_events: List[CookEvent]
    slots: List[SlotSpec]
    targets: Dict[str, dict]
    failures: Dict[str, str] = Field(
        default_factory=dict,
        description="day -> error, for days whose generation failed outright",
    )
    sunday_prep_session: Optional[SundayPrepSession] = Field(
        default=None,
        description="Aggregated Sunday batch-prep plan, when enable_sunday_prep is on",
    )
    unique_plants: List[str] = Field(default_factory=list)

    def by_slot(self) -> Dict[str, CookEvent]:
        return {event.slot_id: event for event in self.cook_events}

    def events_on_days(self, days: List[str]) -> List[CookEvent]:
        day_set = set(days)
        return [event for event in self.cook_events if event.day in day_set]

    def day_slot_macros(self, day: str) -> dict:
        """What one person actually eats on `day`, summed across their slots.

        Includes any `skip_estimate` on the day's skipped slots — a meal eaten
        out is still eaten, and leaving it out would show a day at 60% of
        target on the telemetry header when it was actually met by a
        restaurant. `skip_estimate_totals` carries only `MACRO_KEYS`, so the
        `fiber_g` this returns stays whatever the cooked recipes contribute:
        the fibre in a meal nobody cooked isn't estimable, and 0 is the honest
        reading rather than a guess.
        """
        by_slot = self.by_slot()
        events = []
        for slot in self.slots:
            if slot.day != day or slot.mode == MODE_SKIP:
                continue
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = by_slot.get(source_id)
            if event is not None:
                events.append(event)
        totals = sum_serving_macros(events)
        for key, value in skip_estimate_totals(self.slots, day).items():
            totals[key] += value
        return totals


# --------------------------------------------------------------------------
# Macro math (always Python, never the model)
# --------------------------------------------------------------------------


def fit_recipe_to_budget(
    recipe: Recipe, budget: dict, config: Optional[dict] = None
) -> Tuple[Recipe, float]:
    """Resize one serving of a recipe so its calories land on its budget.

    Models pick sensible *ingredients* and implausible *amounts*, and every
    macro is linear in quantity, so a single scale factor fixes the portion
    without touching the dish. It cannot fix a bad macro ratio — a recipe with
    the right calories and the wrong protein split stays wrong, and shows up
    as a visible delta in the day summary rather than being papered over.
    """
    actual = recipe.total_macros["calories"]
    target = budget.get("calories", 0)
    if actual <= 0 or target <= 0:
        return recipe.round_ingredient_quantities(), 1.0

    low, high = planning_rule(config, "portion_trim_limits")
    deadband = planning_rule(config, "portion_trim_deadband")
    factor = target / actual
    factor = min(max(factor, low), high)
    if abs(factor - 1.0) < deadband:
        return recipe.round_ingredient_quantities(), 1.0
    return recipe.resize_by_factor(factor), factor


# Opening words of a storage note we wrote ourselves. Used to tell our note
# apart from a model-authored one when a batch is later resized: ours is stale
# the moment the portion count moves, a model's is about the dish and must
# survive. Worst case a model happens to open its note this way and gets an
# accurate note in place of its own.
STORAGE_NOTE_PREFIX = "Yields "


def storage_note(
    portions: int,
    keeps_for_days: int,
    config: Optional[dict] = None,
    storage_class: Optional[str] = None,
) -> str:
    """How to keep a batch that has to last until the meal that finishes it.

    Empty for a single serving eaten the day it's cooked — there is nothing to
    say, and `scale_to_servings` leaves `prep_notes` alone rather than writing one.

    `storage_class` is the dish's own answer to how long it keeps, so two
    cards in one week can now legitimately say different things — a rice tray
    bake reaches its freeze point two days before a stew does. It resolves
    through `week.fridge_day_gaps`, which is the identical call
    `ui_state.slot_views` makes for the per-card fridge/freezer badge: that
    pair has had to be reconciled twice already (once for `cook_day_index`,
    once for the prep-day origin) and a per-dish window is its third
    opportunity to drift, so they read one function rather than two copies of
    a threshold.

    Says days and never hours — see `week.storage_day_gaps`.
    """
    if portions <= 1 or keeps_for_days <= 0:
        return ""
    safe_days = fridge_day_gaps(storage_class, config)
    storage = (
        "refrigerate in airtight containers"
        if keeps_for_days < safe_days
        else f"refrigerate what you'll eat within {safe_days} days and freeze the rest"
    )
    return (
        f"{STORAGE_NOTE_PREFIX}{portions} portions, eaten across {keeps_for_days} day(s). "
        f"Portion immediately, {storage}; reheat thoroughly before serving."
    )


# Weeknight slots that eat a Sunday-prepped batch show this instead of the
# cook's own prep_time_minutes — reheating/plating a dish that's already
# cooked is a few minutes, not the from-scratch cook time recorded on the day
# it was actually made.
SUNDAY_PREP_REHEAT_MINUTES = 10


def is_sunday_prepped(event: CookEvent, week_plan: WeekPlan) -> bool:
    """Whether `event` was folded into `week_plan`'s Sunday prep session.

    Matches by `event.slot_id` against `SundayPrepSession.candidate_slot_ids`
    — the same slot_ids `generate_sunday_prep_session` picked *before* the
    model was ever called, whether via the anchor-matched path or the legacy
    flag-matched one. That is the ground truth of what the session covers.

    Testing `event.recipe.long_oven_cook`/`bulk_prep_friendly` instead (the
    original approach) is wrong in both directions: the flags are the
    model's own self-reported answer about the dish, not a record of what
    the session actually aggregated, so a stray flag on some other dinner
    claimed a reheat badge it never earned, and — the bug this replaced —
    the anchor itself not reliably setting its own flag even when the
    per-slot directive told it to (see LONG_COOK_ANCHOR_SLOT_DIRECTIVE /
    BULK_PREP_ANCHOR_SLOT_DIRECTIVE) left the real anchor unlabelled.

    A session saved before `candidate_slot_ids` existed has an empty list —
    same pre-migration tolerance `history_styles()` extends to old
    `meal_history.json` entries — so it falls back to the flag check alone.
    """
    session = week_plan.sunday_prep_session
    if session is None:
        return False
    if session.candidate_slot_ids:
        return event.slot_id in session.candidate_slot_ids
    return bool(event.recipe.long_oven_cook or event.recipe.bulk_prep_friendly)


def is_prepped_ahead(event: CookEvent, week_plan: WeekPlan) -> bool:
    """Whether `event`'s food came out of the pan on prep day, not on its slot's day.

    Narrower than `is_sunday_prepped`, and the gap is the shake: it rides
    along in the same session (`find_shake_candidate`) but is only
    *portioned* ahead — each training morning genuinely blends it fresh, so
    its food is exactly as old as its own day says. Only the two batch
    anchors are cooked ahead, and they are a lunch (`bulk_prep_anchor`) and a
    dinner (`long_cook_anchor`) while the shake is always a breakfast, which
    is what lets one meal_type test tell them apart.
    """
    return event.meal_type != "breakfast" and is_sunday_prepped(event, week_plan)


def weeknight_prep_minutes(event: CookEvent, week_plan: WeekPlan) -> int:
    """Active minutes a slot *eating* `event` needs.

    The cook's own card keeps showing `recipe.prep_time_minutes` — that's the
    real work, on the day it happens. A later slot living off the batch only
    reheats/assembles it.
    """
    if is_sunday_prepped(event, week_plan):
        return SUNDAY_PREP_REHEAT_MINUTES
    return event.recipe.prep_time_minutes


def sum_serving_macros(events: Iterable[CookEvent]) -> dict:
    """Per-serving macros of `events`, summed key by key.

    The one place that walks `NUTRIENT_KEYS` to total up `CookEvent`s — every
    caller differs only in *which* events it hands in (a day's slots, just
    the leftovers, every other slot but one), so that selection logic stays
    with the caller and only the summation is shared.

    Totals fibre as well as the budgeted four, which is what puts a day's
    fibre on the telemetry header via `WeekPlan.day_slot_macros`. Callers
    doing budget arithmetic index `MACRO_KEYS` out of the result and never
    see it — see `NUTRIENT_KEYS`.
    """
    totals = {key: 0.0 for key in NUTRIENT_KEYS}
    for event in events:
        serving = event.recipe.per_serving_macros
        for key in NUTRIENT_KEYS:
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


def _carried_leftovers(
    spec: WeekSpec, day: str, events: Dict[str, CookEvent]
) -> List[Tuple[SlotSpec, CookEvent]]:
    """`day`'s leftover slots paired with the cook event each one eats,
    skipping any whose source hasn't been generated yet (a not-yet-resolved
    same-stage leftover, or an earlier stage that failed)."""
    carried = []
    for slot in spec.slots:
        if slot.day != day or slot.mode != MODE_LEFTOVER or not slot.source:
            continue
        event = events.get(slot.source)
        if event is None:
            continue
        carried.append((slot, event))
    return carried


def _leftover_description(slot: SlotSpec, event: CookEvent) -> str:
    serving = event.recipe.per_serving_macros
    return (
        f"{slot.meal_type}: leftovers of \"{event.recipe.name}\" "
        f"(cooked {event.day}) — {serving['calories']:.0f} kcal, "
        f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
        f"{serving['fat_g']:.0f}g fat"
    )


def carried_macros(
    spec: WeekSpec, day: str, events: Dict[str, CookEvent]
) -> Tuple[dict, List[str]]:
    """Macros already locked in for `day` by leftovers cooked on earlier days,
    plus human-readable descriptions of those meals for the prompt."""
    carried = _carried_leftovers(spec, day, events)
    descriptions = [_leftover_description(slot, event) for slot, event in carried]
    return sum_serving_macros(event for _, event in carried), descriptions


def carried_descriptions(spec: WeekSpec, day: str, events: Dict[str, CookEvent]) -> List[str]:
    """Just the prompt lines `carried_macros` builds, for the meal-type axis
    (`_generate_meal_type_events`), which has no use for the totals — it
    tracks the day's remaining budget in `daily_macro_budgets` instead, so the
    macro half of `carried_macros` was a full `NUTRIENT_KEYS` walk computed
    and thrown away on every call."""
    return [
        _leftover_description(slot, event)
        for slot, event in _carried_leftovers(spec, day, events)
    ]


def logged_intake_for(
    day: str, biometrics: Optional[dict], now: Optional[datetime] = None
) -> Optional[dict]:
    """What Cronometer says was actually eaten on `day`, if `day` is today.

    `biometrics.json`'s `daily_actuals` rows are the measured counterpart to a
    plan's forecast — `{"date": "2026-08-16", "calories": 1120, "protein_g":
    98, ...}`, written by `CronometerSyncService`. When one exists for today,
    it beats the plan as a statement of what has been consumed: the plan says
    what was *meant* to be eaten, and the log says what was.

    Returns None — meaning "nothing measured, use the plan" — in every case
    where it can't be sure:

    - **`day` is not today.** A weekday name is all a `SlotSpec` carries, so
      "Thursday" in a week being planned ahead is not the Thursday that was
      logged. Regenerating a future meal against today's lunch would subtract
      a meal from a day it was never eaten on.
    - **No row for today's date**, or a row whose macros are all zero or
      missing — a partial sync that wrote a dated shell must read as "no data",
      not as "you have eaten nothing today", which would hand the model the
      entire day's budget for one meal.

    Absent keys resolve to 0 rather than dropping the row: a log with calories
    and protein but no fat figure is still a real, useful measurement, and 0 is
    the honest reading of "no fat recorded" inside a row that recorded
    something else.
    """
    now = now or datetime.now()
    if day != now.strftime("%A"):
        return None

    today = now.strftime("%Y-%m-%d")
    rows = [
        row
        for row in ((biometrics or {}).get("daily_actuals") or [])
        if isinstance(row, dict) and str(row.get("date") or "")[:10] == today
    ]
    if not rows:
        return None

    # Last wins: `_upsert_dated_entry` keeps one row per date, so a second is
    # only possible in a hand-edited file, where the later line is the edit.
    row = rows[-1]
    logged = {}
    for key in MACRO_KEYS:
        value = row.get(key)
        logged[key] = float(value) if isinstance(value, (int, float)) else 0.0
    if not any(value > 0 for value in logged.values()):
        return None
    return logged


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def api_key_error() -> Optional[str]:
    """Why generation can't start, or None if it can.

    Split out of `build_client` so a caller can ask *before* committing to a
    run. `generate_week_plan` turns a per-day exception into a per-day failure
    (see "a failed meal must not fail the week"), which is exactly wrong for a
    missing key: it isn't a flaky provider, it will fail every day identically,
    and the user would wait through seven attempts to be told so seven times.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "your_openrouter_api_key_here":
        return "OPENROUTER_API_KEY is not set. Add it to your .env file."
    return None


def _require_models_config(models_config: dict, *keys: str) -> None:
    """models.json is the only source for these values now — no in-code
    fallback. An empty or incomplete models.json must fail loudly here,
    not drift silently onto an outdated hardcoded model."""
    missing = [key for key in keys if not models_config.get(key)]
    if missing:
        raise ValueError(
            f"models.json is missing required key(s): {', '.join(missing)}. "
            "Set them in models.json — there is no built-in fallback."
        )


def model_metadata(config: dict, model: str) -> dict:
    """What models.json records *about* one model id, as opposed to which
    model to use.

    Its `models` table doubles as the selectable list (the drawer offers its
    keys) and as the home for per-model quirks, so an entry with nothing
    unusual about it is simply `{}`. An id absent from the table — a
    hand-typed `--model`, say — has no recorded quirks, which is the same
    answer as an empty entry.
    """
    return ((config.get("models") or {}).get("models") or {}).get(model) or {}


def selectable_models(models_config: dict) -> List[str]:
    """The model ids the UI offers, in models.json's own order."""
    return list((models_config.get("models") or {}).keys())


def build_client(models_config: Optional[dict] = None) -> instructor.Instructor:
    """`models_config` is the loaded `models.json` (or a dict-alike with the
    same keys) — pass `config.get("models")` from a caller that already
    merged it in."""
    models_config = models_config or {}
    error = api_key_error()
    if error:
        raise RuntimeError(error)
    _require_models_config(models_config, "request_timeout_seconds")
    openai_client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=models_config.get("request_timeout_seconds"),
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def build_async_client(models_config: Optional[dict] = None) -> instructor.Instructor:
    """Async twin of `build_client`, for callers that already run on a loop.

    `import_external_recipe` is one call, not seven sequential days, so unlike
    `generate_day` there's no thread-per-call dance to do here — it can
    `await` OpenRouter directly instead of going through `asyncio.to_thread`
    the way a day's generation has to (see "Storage goes through an async
    repository" in CLAUDE.md for why that dance exists at all).
    """
    models_config = models_config or {}
    error = api_key_error()
    if error:
        raise RuntimeError(error)
    _require_models_config(models_config, "request_timeout_seconds")
    openai_client = AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=models_config.get("request_timeout_seconds"),
    )
    # MD_JSON, not JSON/TOOLS — same reason as build_client: Recipe nests
    # Ingredient, and several free OpenRouter providers 422 on the $defs/$ref
    # a schema-carrying mode emits for a nested model.
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def _require_known_model(models_config: dict, role: str, model: str) -> None:
    """A model id *named by models.json* must also appear in its `models` table.

    The table is where per-model quirks live, and `reasoning_required` is the
    one that decides whether a request omits the reasoning switch or sends
    `enabled: False`. `model_metadata` returns `{}` both for a model with
    nothing unusual about it and for one the table has never heard of — which
    is the right answer for a hand-typed `--model` (see `resolve_planner_model`)
    and exactly the wrong one for a standing choice in the file. There the two
    readings differ completely: a model needing the exception gets silently
    treated as ordinary, and every call fails with the hard 400 in CLAUDE.md's
    "Some providers reject the disable switch outright".

    That is not hypothetical — it is what `recipe_parser_model` pointing at
    `google/gemini-3.6-flash` while the table described only
    `google/gemini-3.7-flash` did to recipe import. Raising here moves the
    failure to load time and names both halves, the same loud-failure policy
    `_require_models_config` already applies to a missing key.
    """
    table = models_config.get("models") or {}
    if model not in table:
        known = ", ".join(sorted(table)) or "(the table is empty)"
        raise ValueError(
            f"models.json sets {role} to '{model}', which its 'models' table does "
            f"not describe, so any per-model quirks it needs would be silently "
            f"dropped. Add an entry for it — `{{}}` if it has nothing unusual, "
            f'`{{"reasoning_required": true}}` if it rejects the reasoning switch. '
            f"Known: {known}."
        )


def resolve_planner_model(config: dict) -> str:
    """The model a meal-generation call should use.

    Two things, deliberately not one. `meal_generation_model` in models.json
    is the standing choice — the model a week is generated with unless
    somebody says otherwise. `config["openrouter_model"]` is that "otherwise":
    a per-run selection injected in memory by the CLI's `--model` flag and the
    NiceGUI drawer's model select, and it is *only* ever in memory. Neither
    front end writes it to a file, which is why the key was removed from
    `AppConfig` — as a config-file field it did nothing except shadow the
    standing choice with a second place to look.

    There is no further fallback: an empty models.json and no selection must
    fail loudly, not silently plan against an outdated hardcoded model.

    Only the *standing* choice is checked against the `models` table
    (`_require_known_model`). A per-run `openrouter_model` is deliberately
    left free-form: `--model` exists precisely to try an id nobody has
    recorded anything about yet, and `model_metadata`'s "absent means no
    recorded quirks" is the documented answer for it. A value written in the
    file is a different thing — it is the model this app runs on by default,
    and it should be described where its quirks are kept.
    """
    models_config = config.get("models") or {}
    override = config.get("openrouter_model")
    if override:
        return override
    model = models_config.get("meal_generation_model")
    if not model:
        raise ValueError(
            "No meal generation model configured: models.json has no "
            "'meal_generation_model' and no --model/drawer selection was made. "
            "Set one in models.json."
        )
    _require_known_model(models_config, "meal_generation_model", model)
    return model


def resolve_recipe_parser_model(config: dict) -> str:
    """The model `import_external_recipe` should use.

    Deliberately does *not* consult `openrouter_model` — that is the meal
    planner's per-run selection (CLI `--model`, the drawer's model select) and
    has nothing to do with parsing a pasted recipe. This is a second role, not
    a second opinion about the same one: parsing text into a `Recipe` is cheap
    and mechanical, so it can run on a fast model regardless of which (usually
    pricier) model the week is generated with.
    """
    models_config = config.get("models") or {}
    role = "recipe_parser_model"
    model = models_config.get(role)
    if not model:
        # Falling back names the *other* key, so the error has to as well — a
        # message blaming `recipe_parser_model` for a value it never held sends
        # the reader to the wrong line of the file.
        role = "meal_generation_model"
        model = models_config.get(role)
    if not model:
        raise ValueError(
            "No recipe parser model configured: models.json has neither "
            "'recipe_parser_model' nor 'meal_generation_model' set."
        )
    _require_known_model(models_config, role, model)
    return model


async def load_config_with_models(repository: PlanRepository) -> dict:
    """`load_config()` validated through `AppConfig`, plus `load_models_config()`
    merged under `config["models"]`.

    One call so every caller that needs a *usable* config — CLI, recipe
    import, the NiceGUI app at startup — gets the same schema validation and
    the same model selection, instead of each remembering to also load
    models.json (or skipping validation entirely). `models` is added after
    `load_app_config` returns, not before: it isn't part of config.json's own
    schema, it's a separate file merged in for caller convenience, the same
    way `nudge_foods`/`training_notes` are added to a config dict later at
    generation time.
    """
    raw = await repository.load_config()
    config = load_app_config(raw)
    config["models"] = await repository.load_models_config()
    return config


async def import_external_recipe(
    raw_input: str,
    config: Optional[dict] = None,
    repository: Optional[PlanRepository] = None,
) -> Recipe:
    """Parse pasted recipe text (or a scrape) into a typed, validated Recipe.

    `config` lets a caller that already has one (the NiceGUI drawer,
    mid-session) skip a reload; left out, one is loaded fresh so this also
    works as a standalone call. Dietary rules are enforced the same way
    generation enforces them — nova_group and banned_ingredients read
    `info.context["config"]` (see `Ingredient`'s validators) — so an imported
    recipe answers to the same rules a generated one does, not a weaker set.

    Unlike `generate_day`, there's no day budget to trim against: an imported
    recipe is reported as written, servings included, with ingredient
    quantities and macros for the FULL recipe at that serving count — exactly
    what `Recipe`/`Ingredient` already assume elsewhere (`per_serving_macros`
    divides by `servings`), so no extra scaling step belongs here. A caller
    dropping this into a specific slot (see `PlannerState.swap_slot_with_favorite`)
    normalises to one serving and rescales there, same as it would for any
    other favorite.
    """
    if config is None:
        config = await load_config_with_models(repository or LocalJSONRepository())

    dietary_rules = config["dietary_rules"]
    client = build_async_client(config.get("models"))

    system_prompt = (
        "You turn unformatted recipe text — pasted from a website, a photo's "
        "OCR, a handwritten note — into structured, precise data. Extract "
        "exactly one recipe.\n\n"
        "Rules:\n"
        "- Convert every quantity to grams (quantity_g). Normalize cups, "
        "tablespoons, teaspoons, ounces, pounds and count-based amounts "
        "('1 onion', '2 eggs') using standard ingredient densities/weights — "
        "never leave a non-metric unit in the output.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Classify honestly — if the source is genuinely an ultra-processed "
        "product (Group 4), classify it as 4 rather than mislabeling it; the "
        "schema will reject it rather than let it through unnoticed.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients']) or '(none configured)'}.\n"
        "- Report calories, protein_g, net_carbs_g and fat_g for every "
        "ingredient. If the source doesn't state an ingredient's macros, "
        "estimate them from standard nutrition data for that food and "
        "quantity — every macro field is required and must be a real number, "
        "never null or omitted, even when your best estimate is 0.\n"
        "- `servings` is however many portions the recipe as written yields "
        "(read it off the source if stated, e.g. 'serves 4'; otherwise your "
        "best judgement, minimum 1). Ingredient quantities and macros are for "
        "the FULL recipe at that serving count, not for one serving.\n"
        "- If meal_type isn't stated, infer breakfast/lunch/dinner/snack from "
        "the dish itself.\n"
        "- Do not invent ingredients or steps absent from the source, and add "
        "no commentary — respond with the structured data only."
    )

    model = resolve_recipe_parser_model(config)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("import_external_recipe: requesting parse from %s", model)
    started = time.monotonic()
    try:
        recipe, completion = await client.chat.completions.create_with_completion(
            model=model,
            response_model=Recipe,
            max_retries=3,
            max_tokens=max_tokens,
            extra_body=reasoning_extra_body(model, config),
            context={"config": config},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_input},
            ],
        )
    except Exception as exc:
        logger.warning(
            "import_external_recipe: failed after %.1fs — %s",
            time.monotonic() - started,
            short_error(exc),
        )
        raise

    log_completion("import_external_recipe", completion, started)
    return recipe


def split_targets(
    remaining: dict,
    cook_slots: List[SlotSpec],
    multiplicity: Dict[str, int],
    config: dict,
    overrides: Optional[Dict[str, dict]] = None,
    fiber_target_g: Optional[float] = None,
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

    A third pass then lifts every un-pinned *cooked* slot to
    `planning_rules.min_meal_protein_g` where the day can afford it — see
    `apply_protein_floor`. Weight alone gives the 0.10-weighted snack ~14 g of
    a 144 g day, which is a snack with no protein source in it.

    **`fiber_target_g` is a fourth pass and deliberately a separate axis**, run
    by `split_fibre_share` after everything above. Omit it — as
    `PlannerState.default_skip_estimate` and every pre-target caller does — and
    no budget carries `fiber_g` at all, which is byte-identical to before fibre
    had a target. Three things follow from it being separate rather than a
    fifth `MACRO_KEYS` entry, and all three are the point:

    - **A `meal_overrides` pin does not pin fibre.** An override is a fixed
      *energy* budget, which is the thing `meal_overrides` exists to state, and
      energy says nothing about fibre — so a pinned meal takes its weighted
      share of the day's fibre like any other. Allowing a fifth optional key in
      an override would put a number there with no identity to check it
      against.
    - **Every cook slot shares it, pinned or not**, for the same reason.
    - **The protein floor has no fibre counterpart.** That floor exists because
      protein is dose-limited *per meal*; fibre is not, so a day that reaches
      its figure across three meals has reached it.
    """
    overrides = overrides or {}
    weights_config = config["meal_weights"]

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
        # Every slot pinned by an override still owes the day its fibre — the
        # pin is a statement about energy, not about the whole plate.
        return split_fibre_share(
            budgets, cook_slots, multiplicity, config, fiber_target_g
        )

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
        return split_fibre_share(
            budgets, cook_slots, multiplicity, config, fiber_target_g
        )

    budgets.update(
        {
            slot.id: {key: left[key] * base[slot.id] / total_weight for key in MACRO_KEYS}
            for slot in flexible
        }
    )
    budgets = apply_protein_floor(
        budgets,
        [slot for slot in flexible if slot.mode == MODE_COOK],
        multiplicity,
        planning_rule(config, "min_meal_protein_g"),
    )
    return split_fibre_share(budgets, cook_slots, multiplicity, config, fiber_target_g)


def split_fibre_share(
    budgets: Dict[str, dict],
    cook_slots: List[SlotSpec],
    multiplicity: Dict[str, int],
    config: dict,
    fiber_target_g: Optional[float],
) -> Dict[str, dict]:
    """Add each slot's `fiber_g` share of the day's fibre target to `budgets`.

    The same `meal_weights` the macro split uses, normalised over the same
    slots, so a bigger meal is asked for more fibre — which is honest, since a
    bigger meal is more food. Returns `budgets` untouched when
    `fiber_target_g` is None, and that is the whole of the migration story:
    every caller that has not been taught about the target gets exactly the
    dict it got before.

    **A slot eaten more than once takes a proportionally larger share of the
    day while its own figure stays one serving**, the identical rule the macro
    split applies — the day's fibre is eaten as many times as the meal is.

    An early return for a non-positive target rather than a division guard:
    zero fibre asked for is a day with no fibre target, and writing `0.0` onto
    every slot would put a figure in the prompt that reads as an instruction to
    avoid fibre.
    """
    if fiber_target_g is None or fiber_target_g <= 0 or not cook_slots:
        return budgets

    weights_config = config["meal_weights"]
    weight = {
        slot.id: weights_config.get(slot.meal_type, 0.25) or 0.25 for slot in cook_slots
    }
    total = sum(weight[slot.id] * multiplicity.get(slot.id, 1) for slot in cook_slots)
    if total <= 0:
        return budgets

    adjusted = dict(budgets)
    for slot in cook_slots:
        if slot.id not in adjusted:
            continue
        adjusted[slot.id] = dict(
            adjusted[slot.id], fiber_g=fiber_target_g * weight[slot.id] / total
        )
    return adjusted


def apply_protein_floor(
    budgets: Dict[str, dict],
    slots: List[SlotSpec],
    multiplicity: Dict[str, int],
    floor_g: float,
) -> Dict[str, dict]:
    """Redistribute protein between `slots` so none is briefed below `floor_g`.

    The day's protein total is fixed (locked to the target weight by
    `hydrate_dynamic_targets`), so this moves grams *between* meals rather than
    creating any: slots under the floor are raised to it, and the shortfall is
    taken from the slots above it in proportion to how far above they are. The
    day's protein sums to exactly what it did before.

    **Calories move with the protein, 4 kcal per gram.** Each slot's budget is
    internally consistent (`calories ~= 4p + 4c + 9f`) because `split_targets`
    scales all four macros by the same share, and a `DayRecipes` validator
    later checks the response against that calorie figure. Shifting protein
    alone would break the identity on both sides of the transfer; carrying its
    energy with it leaves carbs and fat untouched, keeps every slot
    reconcilable, and conserves the day's calories as exactly as its protein.

    **Pinned slots are excluded by the caller**, along with leftovers: an
    override is a fixed budget by definition, and a leftover's protein comes
    from the recipe its source already cooked, not from a briefed budget.

    Nothing happens at all when the floor is unreachable — when the slots above
    it don't between them hold enough surplus to lift the ones below. Raising
    some meals and not others would be an arbitrary choice about which meal
    gets short-changed, and a day that genuinely can't carry `n x floor_g` of
    protein is a target problem, not a split problem. Same policy as the
    overspent-override branch above: log it, leave the numbers visible.
    """
    if floor_g <= 0 or len(slots) < 2:
        # One slot already holds the whole flexible remainder — there is
        # nowhere to move grams from, so a shortfall is the day's, not the
        # split's.
        return budgets

    # Day-level grams, so a meal eaten twice is weighed by what it actually
    # costs the day while its own briefed budget stays one serving.
    times = {slot.id: multiplicity.get(slot.id, 1) for slot in slots}
    deficit = {
        slot.id: max(0.0, (floor_g - budgets[slot.id]["protein_g"]) * times[slot.id])
        for slot in slots
    }
    surplus = {
        slot.id: max(0.0, (budgets[slot.id]["protein_g"] - floor_g) * times[slot.id])
        for slot in slots
    }
    needed = sum(deficit.values())
    available = sum(surplus.values())
    if needed <= 0:
        return budgets
    if available < needed:
        logger.warning(
            "protein floor of %.0fg per meal needs %.0fg more than %s can spare — "
            "leaving the weighted split alone",
            floor_g, needed - available, ", ".join(sorted(slot.meal_type for slot in slots)),
        )
        return budgets

    adjusted = dict(budgets)
    for slot in slots:
        if deficit[slot.id] > 0:
            moved = deficit[slot.id]
        elif surplus[slot.id] > 0:
            moved = -surplus[slot.id] * needed / available
        else:
            continue
        per_serving = moved / times[slot.id]
        budget = budgets[slot.id]
        adjusted[slot.id] = dict(
            budget,
            protein_g=budget["protein_g"] + per_serving,
            calories=budget["calories"] + per_serving * 4,
        )
    return adjusted


def cap_to_weighted_share(
    budget: dict, apriori: Optional[dict], config: Optional[dict] = None
) -> Tuple[dict, bool]:
    """Stop the cascade dumping every earlier meal's drift on the last one.

    `generate_week_plan` subtracts each stage's **actual** output from the day
    before splitting the remainder across the meal types still pending. That is
    the right rule — a later meal should aim at what is genuinely left, not at
    a static weight guess — but it has an end effect: by the final stage there
    is exactly one pending slot, so it inherits the whole accumulated
    difference between what the earlier meals were briefed and what they
    actually came back as. `apply_protein_floor` cannot moderate it either, as
    it returns early below two slots: there is nowhere left to move grams from.

    Calories mostly self-correct — `fit_recipe_to_budget` lands each recipe on
    its budget, so the drift is a few percent a meal. **Protein does not.** A
    single scale factor resizes a portion without changing its macro *ratio*
    (that limit is stated outright in `fit_recipe_to_budget`), so a dinner that
    hits its calories 20 g of protein light passes every check and hands that
    20 g straight down the cascade. Three meals of that ask a snack for 90 g of
    protein in a 200 kcal slot — the exact "snack with no protein source"
    failure `min_meal_protein_g` exists to prevent, arrived at from the
    opposite direction, and one nothing downstream would reject:
    `reject_untrimmable_macro_miss` only ever checks calories.

    So the briefed budget is capped at `planning_rules.max_meal_share_multiple`
    x what that meal would have been given had nothing drifted (`apriori` —
    `split_targets` run against the day's full target). Every macro is scaled
    by the same factor rather than calories alone, because each slot's budget
    has to stay internally consistent (`calories ~= 4p + 4c + 9f`) for
    `reject_untrimmable_macro_miss` to check a response against it.

    **The capped surplus is deliberately not reallocated anywhere.** There is
    nowhere to put it — every other meal that day is already cooked — so the
    day lands visibly under target instead. That is this codebase's standing
    answer whenever the numbers don't reconcile: an orphaned leftover
    contributes 0 macros and shows as a shortfall, `apply_protein_floor` does
    nothing and logs when the floor is unaffordable, an overspent
    `meal_overrides` floors the rest at 0 and warns. A day that cannot be made
    to total its target is a target problem, and distorting one meal into a
    900 kcal "snack" to hide it is worse than showing the gap.

    Returns the budget and whether it was capped, so the caller can say so.
    """
    if not apriori:
        return budget, False
    multiple = planning_rule(config, "max_meal_share_multiple")
    ceiling = apriori.get("calories", 0) * multiple
    calories = budget.get("calories", 0)
    if ceiling <= 0 or calories <= ceiling:
        return budget, False
    factor = ceiling / calories
    return {key: budget[key] * factor for key in MACRO_KEYS}, True


def inventory_entries(config: dict) -> List[Tuple[str, Optional[float]]]:
    """`inventory_to_clear` as `(name, grams or None)` pairs.

    The list takes two shapes and always has: a bare string is an unquantified
    item ("half a bag of spinach"), and `{"item": ..., "quantity_g": ...}` is
    one the ledger can actually reason about. Both are legal on purpose — a
    quantity is genuinely unknown for some things, and requiring one would
    make the honest answer unexpressible. A string keeps exactly the behaviour
    this list had before quantities existed: named as a priority every day,
    never spent, never exhausted.

    Grams rather than counts, for the same reason every other quantity in this
    app is grams: the ledger is spent against `Ingredient.quantity_g`, and a
    tin that had to be converted to grams somewhere would be converted twice
    and differently. A tin of tuna is about 95 g drained.

    A malformed entry is dropped with a warning rather than raised on — the
    same policy `split_targets` applies to a malformed `meal_overrides`, and
    for the same reason: a config typo must not cost a week of generation.
    """
    entries: List[Tuple[str, Optional[float]]] = []
    # `.get`, not `[...]`: `AppConfig` gives this field a `default_factory`, so
    # a config with no `inventory_to_clear` at all is legal and a reader has to
    # agree — the same fallback `planning_rule` documents for its own section.
    for entry in config.get("inventory_to_clear") or []:
        if isinstance(entry, dict):
            name = str(entry.get("item") or "").strip()
            raw = entry.get("quantity_g")
            if not name:
                logger.warning("inventory_to_clear: ignoring entry with no item name: %r", entry)
                continue
            try:
                quantity = None if raw is None else float(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "inventory_to_clear: ignoring unreadable quantity_g %r for %s", raw, name
                )
                quantity = None
            if quantity is not None and quantity <= 0:
                quantity = None
            entries.append((name, quantity))
            continue
        name = str(entry).strip()
        if name:
            entries.append((name, None))
    return entries


def seed_inventory_ledger(config: dict) -> Dict[str, float]:
    """`{item name: grams remaining}`, for the quantified entries only.

    The week-wide count each generation stage spends and passes on — the same
    shape as `seafood_used` for `sourcing.max_seafood_meals_per_week`, and the
    same reason it has to be counted rather than merely stated: no single
    generation call sees more than its own axis, so handing every meal type
    the full pantry is what let four of them write the same tin of tuna into
    four different recipes with nothing tracking that it was spent the first
    time.

    A dict where the seafood cap is an int, which is the only real difference:
    an item runs out on its own without taking the others with it.

    Unquantified entries are deliberately absent. There is no number to
    decrement, so a ledger holding them would have to invent one, and
    `inventory_instruction` names them every stage exactly as it always did.
    """
    return {
        name: quantity
        for name, quantity in inventory_entries(config)
        if quantity is not None
    }


def spend_inventory(
    ledger: Dict[str, float], events: Iterable["CookEvent"]
) -> Dict[str, float]:
    """Draw `events`' ingredients down against `ledger`, in place. Returns what was spent.

    Counted per cook event rather than per slot that eats it, the same call
    `is_seafood_meal` makes for the seafood cap: a bulk-cooked tray of thighs
    feeding three lunches came out of the fridge once. `CookEvent.recipe` is
    already scaled to its full batch by `build_cook_event`, so the grams here
    are the grams the shopping list would buy — the two read the same numbers,
    which is what stops the ledger and the list disagreeing about one dish.

    Floored at 0 rather than going negative: a recipe legitimately calls for
    more than the house holds, and the honest reading of that is "the item is
    gone", not "you owe 200 g". The overshoot is not tracked because nothing
    would read it — the item is off the priority list either way.

    **This ledger is not what the shopping list subtracts.** It says how much
    of the pantry a *generated week* reached for, and it dies with the run;
    `shopping.apply_pantry` answers the different question of what is still
    worth buying, from the hand-edited `inventory_to_clear` itself, on every
    repaint. Two counts derived from one config, neither stored — which is
    what keeps the run-scoped argument for this one intact while the list
    still stops asking you to buy chicken you already have.

    Matching is `shopping.ingredient_draws_on`, so the ledger cannot disagree
    with the shopping list about whether two names are the same food.
    """
    spent: Dict[str, float] = {}
    for event in events:
        for ingredient in event.recipe.ingredients:
            for name in ledger:
                if ledger[name] <= 0 or not ingredient_draws_on(name, ingredient.name):
                    continue
                used = min(ledger[name], float(ingredient.quantity_g))
                ledger[name] -= used
                spent[name] = spent.get(name, 0.0) + used
                # One ingredient draws on at most one pantry item: two entries
                # both matching it would otherwise each be charged the full
                # amount, spending the house's stock twice over on one dish.
                break
    return spent


def inventory_instruction(config: dict) -> str:
    """Prompt line telling the model to build around food already in the house.

    `inventory_to_clear` holds things to use up. An entry may be a bare string
    ("half a bag of spinach") or carry a quantity in grams; see
    `inventory_entries`. It is a priority, never a constraint: the styles,
    cuisines and macro budgets still win, because a model told it *must* use an
    item will wedge it into a breakfast where it doesn't belong.

    **A quantified item states what is left, not what was there**, read off
    `config["inventory_ledger"]` — injected per run and decremented between
    stages by `generate_week_plan`, the same in-memory-only channel
    `select_nudge_foods` uses for `nudge_foods` and on the same terms: it must
    never reach disk, and `save_config_keys` is the one write path that could
    take it there. An exhausted item drops out of the line entirely rather than
    being announced as gone, since the sentence is a list of things to reach
    for and a spent item is not one.

    Without a ledger (a single-meal regeneration, or a list with no quantities
    in it) every entry is named exactly as it was before quantities existed.

    Note these items are still costed as ordinary ingredients, so they appear
    on the shopping list — the list says what a recipe needs, not what you have
    yet to buy. Subtracting the pantry from it is a separate change and needs
    the ledger to survive the run, which it deliberately does not.
    """
    ledger = config.get("inventory_ledger")
    items: List[str] = []
    quantified = False
    for name, quantity in inventory_entries(config):
        if quantity is None or ledger is None or name not in ledger:
            items.append(name)
            continue
        remaining = ledger[name]
        if remaining <= 0:
            continue
        items.append(f"{name} ({remaining:.0f}g left)")
        quantified = True
    if not items:
        return ""
    # Only stated when an amount is actually shown. A sentence explaining how
    # to read quantities, in a line carrying none, is a rule about nothing —
    # and every rule in this prompt costs attention the recipe needs.
    amount_clause = (
        "Where an amount is given that is all there is, so do not plan more "
        "of it than that across the meals in this request. "
        if quantified
        else ""
    )
    return (
        "- We already have these at home and want them used up first — prefer "
        "them over buying more of the same kind of thing, and spread them "
        f"across the day's meals where they genuinely fit: {', '.join(items)}. "
        f"{amount_clause}"
        "Never force one into a meal it doesn't suit, and never break a meal's "
        "style, cuisine or macro budget to use one up.\n"
    )


# How many whfoods.json entries to nudge the model toward per generation run
# (see `select_nudge_foods`). ~12 is enough to give the model real choice
# across a week of meals without dominating the prompt or the day's flavour
# profile.
NUDGE_FOOD_SAMPLE_SIZE = 12


async def select_nudge_foods(
    repository: Optional[PlanRepository] = None,
    config: Optional[dict] = None,
    count: int = NUDGE_FOOD_SAMPLE_SIZE,
) -> List[str]:
    """A random sample of nutrient-dense whole foods (whfoods.json) to nudge
    generation toward this run.

    Sampled once per run, not once per day or slot: `build_slot_brief` reads
    the same list off `config["nudge_foods"]` for every slot, so the
    directive names one consistent dozen foods across the week's meals
    instead of a different set per recipe. An empty/missing whfoods.json
    (older checkout, fresh install) resolves to an empty list, which
    `build_slot_brief` treats as "say nothing" — the same tolerance
    `inventory_instruction` extends to an empty pantry list.

    **`config` is what stops the corpus contradicting the rules.** whfoods.json
    is a shipped, location-blind reference — it lists mustard greens, halibut
    and scallops beside broccoli and eggs — and `build_slot_brief` puts the
    sample in *every* slot's brief under "prioritize incorporating these",
    which is a stronger and far more specific signal than any rule in
    `build_generation_rules`. Left unfiltered it nominated foods
    `banned_ingredients` forbids: a prompt that demanded cod two lines after
    banning it, and burned a retry when the model complied. Filtering here
    rather than pruning the corpus keeps whfoods.json a reference someone
    else's config can use in full, and makes `banned_ingredients` the single
    lever — ban an item and it stops being suggested as well as being
    rejected. Matching mirrors `Ingredient.reject_banned_ingredients`:
    case-insensitive substring, same accepted false positives.
    """
    foods = await (repository or LocalJSONRepository()).load_whfoods()
    banned = ((config or {}).get("dietary_rules") or {}).get("banned_ingredients") or []
    if banned:
        lowered = [term.lower() for term in banned]
        foods = [
            food for food in foods
            if not any(term in food.lower() for term in lowered)
        ]
    if not foods:
        return []
    return random.sample(foods, min(count, len(foods)))


# How each `location_rules[...].restrictions` tag reads to the model. A tag is
# a config token, not prose, and translating it here rather than sending the
# bare word is what stops "portable" being read as a brand of lunchbox.
#
# Deliberately absent: any tag about reheating. The office has a microwave, so
# an Office lunch is free to be last night's curry — which is also why
# `location_rules.Office` sets `lunch_mode: leftover`. A "no_reheat" tag
# alongside that would contradict it, and the two together can only be
# reconciled with a per-recipe "edible cold" signal that doesn't exist.
LOCATION_RESTRICTION_PHRASES = {
    "portable": (
        "must travel in a container and be eaten away from a kitchen — "
        "nothing that needs plating, assembling on the spot, or a knife and fork "
        "at a table"
    ),
    "no_long_prep": "must need no preparation at the eating location beyond reheating",
    "quick_cook": "must come together quickly, in one pan where possible",
}


def build_location_note(slot: SlotSpec, config: dict) -> str:
    """The bracketed clause describing where this meal is eaten, or "".

    Per slot rather than per call, because location is a fact about one day
    and `generate_meal_type_week` spans seven of them — the same reason
    `training_notes` is keyed by day and meal type. `build_sourcing_rule`
    partitions its days instead, but it can: a shop is open or shut for the
    whole week's worth of a rule, whereas Monday at the office says nothing
    about Tuesday.

    **A location only constrains the meals it declares a mode for.** Being at
    the office all day says nothing about breakfast, which is eaten at home
    before leaving — so a note reading "must travel in a container" on a
    Monday breakfast is simply wrong, and it was the first thing this emitted
    before the scope was narrowed. `location_rules.Office` names `lunch_mode`
    and nothing else, and that key is already the honest statement of which
    meals the location has an opinion about; `Holiday` names all four,
    `Outing` names lunch and dinner. Reusing it here rather than adding a
    parallel `restricted_meals` list keeps the two from ever disagreeing.

    Emits nothing when the day has no location, the location has no rule, the
    rule says nothing about this meal type, or it carries neither restrictions
    nor a note — so a config without `base_schedule` produces a byte-identical
    prompt to before this existed. Structural consequences of a location
    (`lunch_mode`, `dinner_mode`) are not restated: those already reshaped the
    grid in `week.apply_location_modes`, and repeating them would describe the
    slot the model is looking at back to it.
    """
    rule = location_rule(config, slot.day)
    if not rule or f"{slot.meal_type}_mode" not in rule:
        return ""

    phrases = [
        LOCATION_RESTRICTION_PHRASES[tag]
        for tag in rule.get("restrictions") or []
        if tag in LOCATION_RESTRICTION_PHRASES
    ]
    note = str(rule.get("notes") or "").strip()
    if not phrases and not note:
        return ""

    where = location_for(config, slot.day)
    clauses = "; ".join(phrases)
    detail = " ".join(part for part in [clauses, note] if part)
    return f"[Eaten at {where}: {detail}]"


def build_slot_brief(
    slot: SlotSpec, config: dict, times_eaten_today: int, budget: dict,
    pinned: bool = False, batch_directive: str = "",
) -> str:
    """One prompt line describing a single meal the model has to invent.

    The narrow waist of prompt construction: both generation axes
    (`generate_day` across a day's meal types, `generate_meal_type_week` across
    a meal type's days) render every slot through here, so a meal is described
    identically whichever call produces it.

    Assembled as ` | `-joined parts rather than prose because the model reads a
    list of independent constraints, and a missing one (no cuisine, no training
    note) should drop out cleanly instead of leaving a dangling clause. Order
    matters: identity (style/cuisine) first, then the numbers, then the
    bracketed modifiers that qualify them — `[fixed budget…]`, `[eaten Nx
    today…]` and the training note all explain why the budget reads the way it
    does, so they have to follow it. `SHAKE_SLOT_DIRECTIVE` is the one
    bracketed part that comes early, because it qualifies the *style* rather
    than the budget: it is what the style's own description can't say, namely
    that the other shakes in this week exist.

    The budget is always ONE SERVING. `times_eaten_today` tells the model the
    day's arithmetic already accounts for the repeat, so it doesn't helpfully
    double the portion itself — Python scales the batch afterwards
    (`build_cook_event`).
    """
    parts = [f"- {slot.meal_type.upper()}"]
    style_description = styles_for(config, slot.meal_type).get(slot.style or "")
    if slot.style:
        parts.append(f"style: {humanize(slot.style)}")
        if style_description:
            parts.append(f"({style_description})")
    if slot.style == WORKOUT_BREAKFAST_STYLE:
        parts.append(SHAKE_SLOT_DIRECTIVE)
    if batch_directive:
        parts.append(batch_directive)
    if slot.cuisine:
        parts.append(f"cuisine: {humanize(slot.cuisine)} — authentic flavours and technique")
    nudge_foods = config.get("nudge_foods")
    if nudge_foods:
        parts.append(
            "prioritize incorporating these nutrient-dense foods where flavour "
            f"profiles permit: {', '.join(nudge_foods)}"
        )
    parts.append(
        f"budget (one serving): {budget['calories']:.0f} kcal, "
        f"{budget['protein_g']:.0f}g protein, {budget['net_carbs_g']:.0f}g net carbs, "
        f"{budget['fat_g']:.0f}g fat"
    )
    # A separate part from the budget above, not a fifth figure inside it,
    # because the two are different kinds of number and `FIBER_TARGET_RULE`
    # says so: the four are the constraint and this is a goal to reach inside
    # them. `.get` rather than an index — a caller that passed no
    # `fiber_target_g` to `split_targets` has no key here, and the brief is
    # then byte-identical to the one this app sent before fibre had a target.
    fiber_budget = budget.get("fiber_g")
    if fiber_budget:
        parts.append(f"fibre target: {fiber_budget:.0f}g (aim for it within that budget)")
    if pinned:
        parts.append("[fixed budget for this meal — the other meals absorb the rest of the day]")
    if times_eaten_today > 1:
        parts.append(f"[eaten {times_eaten_today}x today, budget already accounts for that]")
    training_note = config.get("training_notes", {}).get(slot.day, {}).get(slot.meal_type)
    if training_note:
        parts.append(training_note)
    location_note = build_location_note(slot, config)
    if location_note:
        parts.append(location_note)
    if slot.day in WEEKEND_DAYS:
        parts.append(
            f"[Weekend meal: multi-step or slow-cooked recipes up to "
            f"{WEEKEND_PREP_LIMIT_MINUTES} minutes allowed.]"
        )
    else:
        parts.append(
            f"[Max prep/cook time: {WEEKNIGHT_PREP_LIMIT_MINUTES} minutes. "
            "Focus on quick weeknight meals.]"
        )
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
    avoid_recipe_names: Optional[List[str]] = None,
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one day's cooked recipes, returned keyed by meal_type.

    Only the slots set to cook are generated. Leftover slots' macros are
    subtracted from the day's target first, so the model is asked for the
    remaining gap rather than a full day it would then overshoot.
    """
    client = build_client(config.get("models"))

    remaining = {key: max(0.0, targets[key] - carried.get(key, 0.0)) for key in MACRO_KEYS}
    # Fibre rides on the same subtraction: a leftover already brings its own,
    # so only the gap is asked of the meals actually being cooked. `.get` on
    # both sides — `targets` predates the fibre target when it comes from a
    # hand-built dict, and `carried` is 0 for a day with no leftovers.
    remaining["fiber_g"] = max(
        0.0, float(targets.get("fiber_g") or 0.0) - float(carried.get("fiber_g") or 0.0)
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
    budgets = split_targets(
        remaining, cook_slots, multiplicity, config, overrides,
        fiber_target_g=remaining["fiber_g"],
    )
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
        + build_generation_rules(
            config,
            days=[day],
            slot_ids=[slot.id for slot in cook_slots],
            style_rule=DAY_STYLE_RULE,
            variety_rule=DAY_VARIETY_RULE,
            budget_rule=DAY_BUDGET_RULE,
            extras=(
                build_avoid_rules(avoid_proteins, avoid_recipe_names)
                + inventory_instruction(config)
                + leftovers_instruction
                + batch_instruction
            ),
        )
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
        + (
            f"- Fibre (a goal, not part of the calorie arithmetic): "
            f"{remaining['fiber_g']:.0f} g\n"
            if remaining.get("fiber_g")
            else ""
        )
    )

    model = resolve_planner_model(config)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("%s: requesting %d recipe(s) from %s", day, len(cook_slots), model)
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=DayRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        # The validator compares against the sum of the per-recipe budgets, not
        # `remaining`: a meal eaten twice in one day contributes its macros
        # twice, so the recipes legitimately total less than the day does.
        context={
            "config": config,
            "day": day,
            "day_budget": {
                key: sum(budget[key] for budget in budgets.values()) for key in MACRO_KEYS
            },
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    log_completion(day, completion, started)

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
        recipe, factor = fit_recipe_to_budget(by_meal_type[slot.meal_type], budgets[slot.id], config)
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {slot.meal_type}: portions resized x{factor:.2f} to hit "
                f"{budgets[slot.id]['calories']:.0f} kcal"
            )
        fitted[slot.meal_type] = recipe
    return fitted


def build_cuisine_continuity_rule(cook_slots_by_day: Dict[str, SlotSpec]) -> str:
    """The prompt's account of this meal type's cuisine blocks, or "".

    Read off the already-resolved slots rather than recomputed from the
    pattern, so a block the user hand-picked in the drawer reads to the model
    exactly like one `pick_cuisine_blocks` laid out, and a week whose blocks
    were broken up by an explicit choice describes what it actually is.
    Relies on `cook_slots_by_day` being in week order, which is how both
    callers build it (`spec.cook_slots()` follows `spec.slots`).

    Returns "" unless some cuisine actually spans more than one day. Seven
    different cuisines have no continuity to describe, and announcing "blocks"
    of one each would leave the prompt asserting a structure the days below it
    contradict — and would swap out `WEEK_STYLE_RULE` for no reason.
    """
    blocks: List[Tuple[str, List[str]]] = []
    for day, slot in cook_slots_by_day.items():
        if not slot.cuisine:
            continue
        if blocks and blocks[-1][0] == slot.cuisine:
            blocks[-1][1].append(day)
        else:
            blocks.append((slot.cuisine, [day]))

    if not any(len(days) > 1 for _, days in blocks):
        return ""

    described = "; ".join(
        f"{humanize(cuisine)} on {', '.join(days)}" for cuisine, days in blocks
    )
    return (
        "- This week is deliberately built from a small number of cuisine "
        f"blocks rather than a different country every night: {described}. "
        "Within a block, build on the same core aromatics, sauces and pantry "
        "staples so one shop covers all of its nights and nothing is opened "
        "for a single meal — then make those nights differ by main protein, "
        "vegetables, cooking method and dish format, never by drifting to "
        "another cuisine. Do not reuse the same heavy spice paste, marinade "
        "or seasoning profile on consecutive nights within a block — rotate "
        "how the shared aromatics are expressed (a dry rub one night, a wet "
        "marinade the next, a fresh finishing sauce another) so consecutive "
        "dinners don't read as the same meal twice.\n"
    )


def generate_meal_type_week(
    meal_type: str,
    cook_slots_by_day: Dict[str, SlotSpec],
    day_budgets: Dict[str, dict],
    config: dict,
    servings_per_meal: int,
    times_eaten_today: Dict[str, int],
    carried_descriptions_by_day: Dict[str, List[str]],
    pinned_days: Optional[List[str]] = None,
    seafood_used: int = 0,
    avoid_proteins: Optional[List[str]] = None,
    avoid_recipe_names: Optional[List[str]] = None,
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one meal type's recipe for every day it's cooked, in one call.

    The transposed twin of generate_day: that asked for one day's several
    meal types sharing a day budget; this asks for one meal type's several
    days, each already carrying its own cascaded budget (see
    generate_week_plan, which computes `day_budgets` fresh at this stage from
    what every earlier-generated meal type actually consumed).

    `cook_slots_by_day` only has entries for days this meal type is actually
    cooked — a leftover or skipped day never reaches here, so the model is
    never asked to invent something Python is about to discard.

    Four of the rules it sends exist only because this axis can see the whole
    week at once: `DINNER_VARIETY_RULE` (protein spread across the nights),
    `build_batch_roast_rule` (which single night gets the long oven cook),
    `build_cuisine_continuity_rule` (which days share a cuisine, and that they
    do so on purpose) and `SHAKE_ROTATION_RULE` (the other shakes this week).
    None of them can be stated by a per-slot brief, and none of them survive
    the per-day axis, where each call sees one day.
    """
    client = build_client(config.get("models"))
    days = list(cook_slots_by_day.keys())
    pinned_days = pinned_days or []

    dinner_variety_instruction = DINNER_VARIETY_RULE if meal_type == "dinner" else ""
    # Whole-week, like the four rules below, but counted rather than stated:
    # `seafood_used` is what the meal types generated before this one actually
    # returned, so the cap is spent in MEAL_TYPE_PRIORITY order instead of
    # being re-offered in full to every axis.
    seafood_instruction = build_seafood_limit_rule(
        config, meal_type, len(days), seafood_used
    )
    long_cook_anchor = config.get("long_cook_anchor")
    bulk_prep_anchor = config.get("bulk_prep_anchor")

    # Each whole-week batch rule goes to the axis its own anchor sits on,
    # rather than to "dinner" on the assumption that both batches are dinners.
    # They are not: bulk prep anchors a *lunch* (see
    # ui_generation.apply_batch_selections) so the week's Monday-Wednesday
    # lunches can all eat one prepped dish, and sending its rule to the dinner
    # call would brief the wrong meal entirely — the lunch call would never
    # hear that one of its slots is a batch, and the dinner call would be told
    # to mark a bulk-prep dish it isn't cooking.
    def anchors_this_axis(anchor: Optional[str]) -> bool:
        return bool(anchor) and parse_slot_id(anchor)[1] == meal_type

    batch_roast_instruction = (
        BATCH_ROAST_ANCHOR_RULE
        if anchors_this_axis(long_cook_anchor)
        else build_batch_roast_rule(config, days)
        if meal_type == "dinner" and config["enable_sunday_prep"]
        else ""
    )
    bulk_prep_instruction = BULK_PREP_RULE if anchors_this_axis(bulk_prep_anchor) else ""
    # Both are whole-week facts a single slot's brief cannot state: which days
    # share a cuisine, and which other breakfasts are also shakes. This call is
    # the only place in the app that can see either.
    cuisine_continuity_instruction = build_cuisine_continuity_rule(cook_slots_by_day)
    shake_days = [
        day for day, slot in cook_slots_by_day.items() if slot.style == WORKOUT_BREAKFAST_STYLE
    ]
    shake_rotation_instruction = SHAKE_ROTATION_RULE if len(shake_days) > 1 else ""

    all_carried_descriptions = [
        f"{day}: {description}"
        for day, descriptions in carried_descriptions_by_day.items()
        for description in descriptions
    ]
    leftovers_instruction = (
        "- These meals are already fixed (cooked earlier this run, or already "
        "leftovers of something cooked earlier this run) and are NOT part of "
        "what you're generating — their macros are already subtracted from "
        "each day's budget below, but keep ingredients varied against them "
        "where you can:\n"
        + "\n".join(f"  * {line}" for line in all_carried_descriptions)
        + "\n"
        if all_carried_descriptions
        else ""
    )
    batch_days = [day for day in cook_slots_by_day if times_eaten_today.get(day, 1) > 1]
    batch_instruction = (
        "- Some days below are eaten more than once that same day. Design "
        "those to portion and reheat well (a tray/pot dish rather than "
        "something that must be served immediately). Still give quantities "
        "for ONE serving; Python scales them to the full batch.\n"
        if batch_days
        else ""
    )

    def batch_directive_for(day: str) -> str:
        target_id = slot_id(day, meal_type)
        if target_id == long_cook_anchor:
            return LONG_COOK_ANCHOR_SLOT_DIRECTIVE
        if target_id == bulk_prep_anchor:
            return BULK_PREP_ANCHOR_SLOT_DIRECTIVE
        return ""

    slot_briefs = "\n".join(
        f"- {day} "
        + build_slot_brief(
            slot,
            config,
            times_eaten_today.get(day, 1),
            day_budgets[day],
            pinned=day in pinned_days,
            batch_directive=batch_directive_for(day),
        ).lstrip("- ")
        for day, slot in cook_slots_by_day.items()
    )

    system_prompt = (
        f"You are a precision meal-planning assistant cooking for "
        f"{servings_per_meal} people. Generate exactly {len(cook_slots_by_day)} "
        f"{meal_type} recipe(s) for the week below — one per day listed, each "
        "matching that day's own budget. Recipes must be realistic, varied and "
        "non-repetitive across the days.\n\n"
        + build_generation_rules(
            config,
            days=days,
            slot_ids=[slot.id for slot in cook_slots_by_day.values()],
            style_rule=(
                WEEK_CUISINE_BLOCK_STYLE_RULE
                if cuisine_continuity_instruction
                else WEEK_STYLE_RULE
            ),
            variety_rule=WEEK_VARIETY_RULE,
            budget_rule=WEEK_BUDGET_RULE,
            extras=(
                dinner_variety_instruction
                + seafood_instruction
                + batch_roast_instruction
                + bulk_prep_instruction
                + cuisine_continuity_instruction
                + shake_rotation_instruction
                + build_avoid_rules(avoid_proteins, avoid_recipe_names)
                + inventory_instruction(config)
                + leftovers_instruction
                + batch_instruction
            ),
            response_shape_rule=WEEK_RESPONSE_SHAPE_RULE,
        )
    )

    user_prompt = (
        f"Generate exactly {len(cook_slots_by_day)} {meal_type} recipe(s), one per "
        f"day below, each hitting its own budget:\n{slot_briefs}\n"
    )

    model = resolve_planner_model(config)
    max_tokens = meal_type_week_max_tokens(model, len(cook_slots_by_day))

    logger.info(
        "%s: requesting %d recipe(s) across %s from %s",
        meal_type, len(cook_slots_by_day), ", ".join(days), model,
    )
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=MealTypeWeekRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        context={
            "config": config,
            "day_budgets": day_budgets,
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    log_completion(meal_type, completion, started)

    missing = [day for day in days if day not in response.recipes]
    if missing:
        raise ValueError(
            f"{meal_type}: model returned no recipe for {', '.join(missing)} "
            f"(got: {', '.join(sorted(response.recipes)) or 'nothing'})"
        )

    fitted = {}
    for day, slot in cook_slots_by_day.items():
        recipe, factor = fit_recipe_to_budget(response.recipes[day], day_budgets[day], config)
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {meal_type}: portions resized x{factor:.2f} to hit "
                f"{day_budgets[day]['calories']:.0f} kcal"
            )
        fitted[day] = recipe
    return fitted


def build_sunday_prep_brief(event: CookEvent, spec: WeekSpec) -> str:
    """One candidate line for the Sunday prep prompt.

    Carries only numbers Python already computed (portions, prep time, which
    days it's eaten, the fridge/freezer storage window) so the model organises
    a cooking session instead of re-deriving any of them. Leads with
    `event.recipe.name` verbatim, which is also the exact string the system
    prompt tells the model to echo back into `meals_included` — one dish per
    candidate line here.
    """
    eaten_days = sorted(
        {parse_slot_id(slot_id)[0] for slot_id in event.eaten_by},
        key=spec.day_index,
    )
    ingredient_list = ", ".join(
        f"{ingredient.name} {ingredient.quantity_g:.0f}g" for ingredient in event.recipe.ingredients
    )
    return (
        f"- {event.recipe.name} ({event.meal_type}, {event.portions} portions, "
        f"{event.recipe.prep_time_minutes} min prep-as-written) — eaten on: "
        f"{', '.join(eaten_days)}. Storage: {event.recipe.prep_notes}\n"
        f"  Ingredients: {ingredient_list}\n"
        f"  Method: {' '.join(event.recipe.instructions)}"
    )


def find_shake_candidate(
    cook_events: Iterable[CookEvent], spec: WeekSpec
) -> Tuple[Optional[CookEvent], int]:
    """The week's first workout shake (by day order) and how many mornings repeat it.

    Shakes are never leftover-linked across mornings the way a dinner batch
    is — each morning still gets its own fresh blend (see `pin_style`, which
    only pins the *style*, not a cook/leftover link) — so there is nothing to
    anchor or spread here. What's worth doing ahead is portioning the shared
    base (protein, liquid — the part `SHAKE_ROTATION_RULE` keeps identical
    across the week) into grab-and-go containers once, rather than measuring
    it out fresh every training morning. Returns the first such event so
    `build_shake_prep_brief` has one real recipe's ingredients to work from,
    plus the count so it can tell the model how many mornings to portion for.
    """
    shake_events = sorted(
        (event for event in cook_events if event.style == WORKOUT_BREAKFAST_STYLE),
        key=lambda event: spec.day_index(event.day),
    )
    if not shake_events:
        return None, 0
    return shake_events[0], len(shake_events)


def build_shake_prep_brief(event: CookEvent, morning_count: int) -> str:
    """The shake's candidate line for the Sunday prep prompt.

    Deliberately not `build_sunday_prep_brief`: a shake isn't cooked ahead or
    leftover-linked (see `find_shake_candidate`), so there is no eaten-on
    span and no storage window to report — only ingredients to portion out,
    once, for however many mornings repeat it.
    """
    ingredient_list = ", ".join(
        f"{ingredient.name} {ingredient.quantity_g:.0f}g" for ingredient in event.recipe.ingredients
    )
    return (
        f"- {event.recipe.name} (breakfast shake, portion-ahead only — repeats, "
        f"with rotating fruit/seed, on {morning_count} morning(s) this week; "
        "NOT cooked or blended ahead)\n"
        f"  Ingredients (one morning's worth): {ingredient_list}"
    )


def generate_sunday_prep_session(
    cook_events: List[CookEvent],
    spec: WeekSpec,
    config: dict,
) -> Optional[SundayPrepSession]:
    """Turn the week's already-generated batch cooks into one Sunday prep timeline.

    Candidates are, at most, the two dinners `ui_generation.
    apply_batch_selections` actually anchored this run (`config["long_cook_
    anchor"]`/`["bulk_prep_anchor"]`, matched by slot_id) — a batch that's
    actually a quick stovetop stir-fry or a no-cook smoothie pack doesn't
    belong in a hands-off Sunday session even though it's eaten across
    several days, and only `long_cook`'s genuinely long, mostly-unattended
    oven roast/bake or slow-cooker braise, or `bulk_prep`'s soup/stew/curry
    (see `Recipe.long_oven_cook`/`bulk_prep_friendly`), are worth folding
    into one aggregated prep block. Matching by slot_id rather than by flag
    alone is what caps this at 2 dishes: the field exists on every recipe
    regardless of axis, so trusting the flag would let an unprompted
    `long_oven_cook`/`bulk_prep_friendly` the model set on some other dinner
    (a stray choice, or a day regenerated after the anchor was picked) pull
    a 3rd dish into the session nothing actually asked for.

    A week with neither anchor set falls back to `long_oven_cook` alone —
    the CLI's legacy `enable_sunday_prep` path, where `build_batch_roast_rule`
    lets the model pick its own day rather than Python anchoring one in
    advance, so there is no slot_id to match against. A week with no
    candidate dish under either path has nothing to aggregate, so this
    returns None rather than an empty session — same "no candidates, no
    prompt" rule `inventory_instruction` uses for an empty pantry list.

    A week's first workout shake rides along automatically whenever a real
    anchor produced at least one dish above (see `find_shake_candidate`) —
    capped at one representative morning, never a trigger on its own. It is
    portioned, not cooked ahead (`build_shake_prep_brief`), since unlike the
    two dinners it is never leftover-linked: each training morning still
    blends its own fresh shake, only the shared base gets bagged up Sunday.

    This only reorganises recipes the day-generation calls already produced —
    it never invents food, so unlike `generate_day` there is no macro budget
    to validate against. The only hard constraint is
    `SundayPrepSession.total_active_minutes <= 120`, enforced by the schema
    itself; `config`'s `max_prep_active_mins` is both the target handed to the
    model in the prompt and the bound `SundayPrepSession` validates against,
    threaded through instructor's `context=` so the two can never disagree.
    """
    long_cook_anchor = config.get("long_cook_anchor")
    bulk_prep_anchor = config.get("bulk_prep_anchor")
    if not (config["enable_sunday_prep"] or long_cook_anchor or bulk_prep_anchor):
        return None

    # When an anchor was actually picked (ui_generation.apply_batch_selections
    # ran), match candidates by slot_id, not by flag alone — each toggle
    # names at most one dinner, so this is what caps the session at 2 dishes
    # regardless of what else the model may have set. `long_oven_cook` has no
    # per-run gate of its own the way `bulk_prep_friendly` does, so without
    # this an unprompted flag on some other dinner (a stray model choice, or
    # a day regenerated after the anchor was picked) could pull a 3rd dish
    # into the session even though nothing asked it to.
    #
    # `enable_sunday_prep` alone (the CLI's legacy path, no anchor known in
    # advance because build_batch_roast_rule lets the model pick freely) still
    # falls back to flag detection — there is no slot_id to match against.
    anchor_ids = {value for value in (long_cook_anchor, bulk_prep_anchor) if value}
    if anchor_ids:
        candidates = [event for event in cook_events if event.slot_id in anchor_ids]
    else:
        candidates = [
            event
            for event in cook_events
            if event.recipe.prep_notes and event.recipe.long_oven_cook
        ]
    if not candidates:
        return None

    # The shake rides along automatically whenever a real anchor produced at
    # least one dinner candidate above — it never triggers a session on its
    # own (see find_shake_candidate's docstring: there's no anchor/spread for
    # it), and it's capped at one representative morning regardless of how
    # many the week actually has, same "at most 1" ceiling the two dinner
    # anchors already give the dish side.
    shake_event, shake_morning_count = (
        find_shake_candidate(cook_events, spec) if anchor_ids else (None, 0)
    )

    max_active = config["max_prep_active_mins"]
    candidate_briefs = "\n".join(build_sunday_prep_brief(event, spec) for event in candidates)
    if shake_event is not None:
        candidate_briefs += "\n" + build_shake_prep_brief(shake_event, shake_morning_count)

    shake_instruction = (
        "- One candidate above is a breakfast shake marked \"portion-ahead "
        "only\" — it is NOT cooked, blended or assembled in this session. "
        "Add exactly one step that portions its base ingredients into "
        "grab-and-go containers/bags for its stated number of mornings; the "
        "blending itself happens fresh each of those mornings, not Sunday. "
        "Include it in meals_included like any other candidate.\n"
        if shake_event is not None
        else ""
    )

    system_prompt = (
        "You are planning ONE Sunday batch-prep session that gets a week's "
        "worth of already-decided batch cooking done in advance. The recipes "
        "below are fixed — do not change ingredients, quantities or methods, "
        "only organise the work of cooking them.\n\n"
        "Rules:\n"
        f"{shake_instruction}"
        f"- Hard cap: total_active_minutes must not exceed {max_active}. Active "
        "minutes are hands-on time (chopping, stirring, portioning, sealing "
        "bags) — time a slow cooker, oven or fridge runs unattended is "
        "passive_minutes, not active_minutes, and does not count against the "
        "cap.\n"
        "- meals_included must list the name of every dish being prepped in "
        "this session (one entry per candidate recipe below, verbatim) — "
        "the aggregated timeline says how to cook them, this says what they "
        "are.\n"
        "- Aggregate identical prep across recipes into one step instead of "
        "repeating it: if three recipes each need a chopped onion, one phase "
        "chops all the onions together rather than three separate steps.\n"
        "- Sequence the timeline chronologically: start the highest-passive-"
        "time tasks first (slow cookers, roasts, anything that simmers or "
        "bakes unattended), so their passive time overlaps with the active "
        "chopping/portioning/bagging work for the other dishes, rather than "
        "the whole session running start to end back to back.\n"
        "- Storage: each candidate's Storage line below already says whether "
        "it's fridge-only or fridge-plus-freezer (Python computed this from "
        "how many days it has to last against how long that particular kind "
        "of dish keeps — a rice or pasta dish keeps half as long as anything "
        "else, so two candidates here can legitimately differ) — do not "
        "recompute it. Any item marked to "
        "freeze must get its own explicit freeze step (portion, label, date, "
        "freeze) in the timeline; note the thaw lead time in that phase's "
        "description (e.g. 'move to fridge the night before eating') rather "
        "than scheduling a thaw step in this session, since thawing happens "
        "later in the week, not on Sunday.\n"
        "- aggregated_ingredients maps a combined prep task to what it covers, "
        'e.g. {"onions": "4 diced (for chilli, bolognese, curry)"} — only '
        "include ingredients that are actually shared prep across two or more "
        "of the candidates; it is not a shopping list.\n"
        "- Do not show your work or narrate — respond with the structured "
        "data only."
    )

    candidate_count = len(candidates) + (1 if shake_event is not None else 0)
    user_prompt = (
        f"This week's batch-prep candidates ({candidate_count}):\n\n"
        f"{candidate_briefs}\n\n"
        "Build the Sunday prep session for exactly these candidates."
    )

    model = resolve_planner_model(config)
    client = build_client(config.get("models"))
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info(
        "sunday_prep: requesting session for %d candidate(s) from %s", candidate_count, model
    )
    started = time.monotonic()
    session, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=SundayPrepSession,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        # The cap the schema validates against — same number the prompt states
        # above, so a rejection is always for a rule the model was given.
        context={"config": config, "max_prep_active_mins": max_active},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    log_completion("sunday_prep", completion, started)
    # `candidates` (plus the shake, if any) is the ground truth of what this
    # session covers — Python picked it, either by slot_id (the anchor path)
    # or by flag (the legacy enable_sunday_prep path) before the model was
    # ever called. Stamping it onto the response is what lets is_sunday_prepped
    # match by slot_id afterwards instead of re-trusting the recipe's own
    # long_oven_cook/bulk_prep_friendly flags for a fact Python already knows.
    covered_ids = [event.slot_id for event in candidates]
    if shake_event is not None:
        covered_ids.append(shake_event.slot_id)
    return session.model_copy(update={"candidate_slot_ids": covered_ids})


def on_calling_loop(callback):
    """Wrap `callback` so a worker thread's call runs back on *this* loop.

    `generate_day` runs in a worker thread (see `generate_week_plan`), which
    means anything it calls back into runs off the event loop. That is fine for
    the CLI's `print`, and not fine for the NiceGUI front end, whose elements
    queue their updates against the loop that owns the client — so the hop is
    undone here, once, rather than being every caller's problem to remember.

    Must be called from the loop thread: it captures the running loop at wrap
    time, and there is none inside the worker.
    """
    if callback is None:
        return None
    loop = asyncio.get_running_loop()

    def forward(*args, **kwargs) -> None:
        loop.call_soon_threadsafe(lambda: callback(*args, **kwargs))

    return forward


def single_serving(recipe: Recipe) -> Recipe:
    """A recipe resized to exactly one serving, whatever it was saved at.

    Everything downstream of generation assumes one serving: every prompt
    says so, `fit_recipe_to_budget` trims against a one-serving budget, and
    `build_cook_event` scales up from one to the batch the slot owes. A saved
    favourite breaks that assumption — it was bookmarked off a card already
    scaled to its portions, so a 2-portion dinner carries `servings: 2` and
    double the ingredients.

    Trimming that against a one-serving budget doesn't merely halve it: the
    needed factor lands outside `portion_trim_limits`, so the clamp fires and
    the recipe comes out at 0.6x — 20% over budget, silently, on every pinned
    favourite. `PlannerState.swap_slot_with_favorite` already normalises for
    exactly this reason; this is the same rule shared rather than copied.
    """
    if recipe.servings == 1:
        return recipe
    return recipe.resize_by_factor(1 / max(1, recipe.servings)).model_copy(
        update={"servings": 1}
    )


def build_cook_event(
    slot: SlotSpec,
    recipe: Recipe,
    spec: WeekSpec,
    portions: Dict[str, int],
    claims: Dict[str, List[str]],
    config: Optional[dict] = None,
) -> CookEvent:
    """Scale one model-generated recipe into the batch its slot owes, as a CookEvent.

    The model always returns ONE serving (every prompt says so); this is where
    that becomes the real batch. Both are derived, never entered: `portions`
    comes from `week.portions_for` (slots claiming this cook x household size)
    and the storage window is the gap to the last slot that eats it, which is
    what decides whether the note says "refrigerate" or "freeze the rest".

    `claims` is passed in rather than recomputed via `week.span_days` because
    every caller already has the whole-week `eaten_on` scan hoisted — calling
    span_days here would redo it once per cook event.

    Shared by all three generation paths (`_generate_day_events`,
    `_generate_meal_type_events`, `regenerate_single_meal`), which had three
    copies of this arithmetic. A recipe scaled by one rule and portioned by
    another is precisely the disagreement derived portions exist to prevent.
    """
    claim_ids = claims.get(slot.id, [slot.id])
    last_day_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claim_ids)
    # Counted from the day the food is actually cooked, which for a batch-prep
    # anchor is prep day rather than its own slot's day (`week.PREP_DAY_INDEX`).
    # Every anchor is day 0, so measuring from the slot was short by exactly
    # one on every prep batch — and the maximum-span one then reported
    # one day short of its window, compared that against the window, and told
    # you to refrigerate the single batch in the week that is sitting at the
    # limit and is the whole reason `storage_note`'s freeze branch exists.
    cook_index = cook_day_index(spec, slot.day, slot.id in prep_day_batch_slot_ids(config))
    scaled = recipe.scale_to_servings(
        portions[slot.id],
        keeps_for_days=last_day_index - cook_index,
        config=config,
    )
    return CookEvent(
        slot_id=slot.id,
        day=slot.day,
        meal_type=slot.meal_type,
        portions=portions[slot.id],
        style=slot.style,
        cuisine=slot.cuisine,
        eaten_by=claim_ids,
        recipe=scaled,
    )


async def _generate_day_events(
    day: str,
    spec: WeekSpec,
    config: dict,
    day_targets: dict,
    portions: Dict[str, int],
    claims: Dict[str, List[str]],
    carry_events: Dict[str, CookEvent],
    avoid_proteins: List[str],
    avoid_recipe_names: List[str],
    note_callback=None,
) -> Dict[str, CookEvent]:
    """Generate and scale one day's cook events, keyed by slot_id.

    Shared by `generate_week_plan` (which walks every day) and
    `regenerate_single_day` (which calls this for just one). `carry_events`
    supplies the cook events any of `day`'s leftover slots point at — for a
    full-week walk that's the days already generated this run; for a single
    day it's whatever is already in the saved plan, since a leftover only
    ever points backwards and those days aren't being touched.

    Raises on failure rather than swallowing it — callers decide whether
    that's fatal (a single-day retry) or recoverable (a week walking on to
    the next day).
    """
    cook_slots = spec.cook_slots_on(day)
    if not cook_slots:
        return {}

    carried, descriptions = carried_macros(spec, day, carry_events)
    protein_avoid_window = planning_rule(config, "protein_avoid_window")
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_day,
        day=day,
        targets=day_targets,
        cook_slots=cook_slots,
        config=config,
        servings_per_meal=spec.servings_per_meal,
        multiplicity=day_multiplicity(spec, day),
        carried=carried,
        carried_descriptions=descriptions,
        avoid_proteins=avoid_proteins[-protein_avoid_window:],
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    return {
        slot.id: build_cook_event(
            slot, recipes[slot.meal_type], spec, portions, claims, config
        )
        for slot in cook_slots
    }


async def _generate_meal_type_events(
    meal_type: str,
    spec: WeekSpec,
    config: dict,
    day_budgets: Dict[str, dict],
    portions: Dict[str, int],
    claims: Dict[str, List[str]],
    carried_descriptions_by_day: Dict[str, List[str]],
    pinned_days: List[str],
    avoid_proteins: List[str],
    avoid_recipe_names: List[str],
    note_callback=None,
    seafood_used: int = 0,
) -> Dict[str, CookEvent]:
    """Generate and scale one meal type's cook events across the week, keyed by slot_id.

    The transposed twin of `_generate_day_events`: that walked one day's
    several meal types, this walks one meal type's several days. `day_budgets`
    is keyed by day and already holds this stage's cascaded budget for
    `meal_type` (see `generate_week_plan`) rather than the day's full target.

    Raises on failure rather than swallowing it, same as `_generate_day_events`
    — `generate_week_plan` decides that's recoverable (mark every day this
    stage would have cooked as failed, move on to the next meal type).
    """
    cook_slots_by_day = {
        slot.day: slot
        for slot in spec.cook_slots()
        if slot.meal_type == meal_type and slot.day in day_budgets
    }
    if not cook_slots_by_day:
        return {}

    times_eaten_today = {
        day: day_multiplicity(spec, day).get(slot.id, 1)
        for day, slot in cook_slots_by_day.items()
    }
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_meal_type_week,
        meal_type=meal_type,
        cook_slots_by_day=cook_slots_by_day,
        day_budgets=day_budgets,
        config=config,
        servings_per_meal=spec.servings_per_meal,
        seafood_used=seafood_used,
        times_eaten_today=times_eaten_today,
        carried_descriptions_by_day=carried_descriptions_by_day,
        pinned_days=pinned_days,
        avoid_proteins=avoid_proteins,
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    return {
        slot.id: build_cook_event(slot, recipes[day], spec, portions, claims, config)
        for day, slot in cook_slots_by_day.items()
    }


# Below this share of a day's calorie target, generate_week_plan's post-loop
# reconciliation check warns rather than staying silent. Set loose enough that
# ordinary portion-trim rounding (Thursday/Friday on the shipped config land
# at 95-105%) never fires it — this is for a day that is *structurally*
# incapable of reconciling (every remaining slot pinned, skipped or fed by
# another day's cook), not day-to-day noise.
UNDER_TARGET_NOTE_THRESHOLD = 0.85


async def generate_week_plan(
    spec: WeekSpec,
    config: dict,
    history: Optional[List[dict]] = None,
    progress_callback=None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Generate the whole week, one API call per meal type that has cooking to do.

    Loops by meal type (`meal_type_order`: breakfast, dinner, lunch, snack)
    instead of by day: each call asks for one meal type's recipe on every day
    it's cooked, all at once. That gives the model full-week visibility for
    protein variety (`DINNER_VARIETY_RULE`) and lets each day's budget
    cascade — after a meal type is generated, its *actual* output (not its
    a-priori share) is subtracted from every affected day's remaining budget,
    so the next meal type's split is computed from the real number rather
    than a static weight guess. Dinner runs before lunch specifically so the
    one cross-meal-type leftover `week.leftover_meal_type_error` allows (a
    lunch eating a dinner's leftovers) always has its source already cooked.

    Cost still scales with what's actually being cooked, just along a
    different axis than before: a meal type nobody cooks this week (every
    slot leftover or skipped) makes no call at all, same as a day with
    nothing to cook used to be free.

    Each meal type's API call is dispatched with `asyncio.to_thread`, same
    reason as before: `generate_meal_type_week` blocks on instructor's
    *synchronous* client for 30s-3min (worse per call than a single day used
    to, since up to 7 recipes are being generated at once), and awaiting it
    inline would hold the loop for that whole span — fatal in NiceGUI, where
    it would freeze every connected browser until the call returned. Meal
    types remain strictly sequential — one thread at a time, in
    `meal_type_order` — because a later meal type's budget is computed from
    every earlier meal type's actual output.

    A whole meal-type call failing marks every day it would have cooked as
    failed (`WeekPlan.failures`, now keyed by slot_id rather than by day —
    see the module docstring's discussion of the trade this accepts:
    fewer, bigger calls means a bad one can cost up to 7 recipes instead of
    the one day's worth a per-day call could lose).
    """
    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()
    # Before `week_targets` and before the first split: every downstream reader
    # of a macro number — `week_targets`, `split_targets`, `meal_overrides_for`,
    # `build_slot_brief`, and the `day_budget` the response validator checks
    # against — reads this one config, so hydrating it here is what makes the
    # whole run aim at the body rather than at the file.
    config = await hydrate_config(config, repository, note_callback)
    nudge_foods = await select_nudge_foods(repository, config)
    if nudge_foods:
        config = dict(config, nudge_foods=nudge_foods)
    rejections = await (repository or LocalJSONRepository()).load_rejections()
    if rejections:
        config = dict(config, rejected_preferences=rejections)

    # Saved favourites claim their slots before anything is generated, so the
    # stage loop below never asks the model for a meal already decided. Done
    # here rather than in `resolve_auto_choices` (where the style and cuisine
    # picks live) purely because it needs the repository, the same reason
    # `hydrate_config` and `select_nudge_foods` sit here.
    # How long each cook's food has to keep, computed once off the settled
    # grid and published in memory to everything downstream. Three readers,
    # one dict, so the brief, the rejection and the favourite gate cannot
    # disagree about a Thursday: `build_storage_rule` tells the model the
    # figure, `reject_short_storage_class` judges the answer against it, and
    # `favorite_keeps_long_enough` applies it to the route neither of those
    # can see. Before the pins below for exactly that third reader; pinning
    # cannot move a span anyway, since it changes what cooks a slot and never
    # which slots eat it.
    config = dict(config, storage_spans=storage_spans(spec, config))

    pinned_recipes: Dict[str, Recipe] = {}
    favorites = await (repository or LocalJSONRepository()).get_favorites()
    for pin_slot_id, record in select_favorite_assignments(
        spec, config, history, favorites
    ).items():
        try:
            # No `context=` here on purpose. A favourite was validated when it
            # was saved, and re-validating against today's `dietary_rules`
            # would reject a dish banned since — which is a real thing to know
            # but not something to discover mid-run, one slot at a time, with
            # no way to act on it. It is skipped instead, with a note.
            pinned_recipes[pin_slot_id] = Recipe.model_validate(record["recipe"])
        except ValidationError as exc:
            logger.warning(
                "favorite %s is not a usable recipe, generating that slot instead: %s",
                record.get("id"), short_error(exc),
            )
            continue
        spec = pin_recipe(spec, pin_slot_id, record.get("id"))
        if note_callback:
            note_callback(
                f"{slot_label(pin_slot_id)}: cooking saved favourite "
                f"\"{record['recipe']['name']}\" — not generating this slot"
            )

    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    # Seeded from previous weeks, then extended as this week generates —
    # otherwise every stage is told to avoid the same stale list and nothing
    # stops all seven dinners being chicken.
    avoid_proteins = recent_main_proteins(history, config)
    # Same seed-then-extend pattern as avoid_proteins, but over the full
    # history_max_entries window (see recent_recipe_names) rather than a
    # short lookback — a recipe name must not repeat at all within it.
    avoid_recipe_names = recent_recipe_names(history)
    protein_avoid_window = planning_rule(config, "protein_avoid_window")
    # Same seed-then-extend shape as avoid_proteins, but a count rather than a
    # list: how much of `sourcing.max_seafood_meals_per_week` earlier stages
    # have actually spent. Counting each stage's real output — instead of
    # handing every meal type the same full cap — is what makes the limit
    # genuinely week-wide, since no single call can see more than its own axis.
    seafood_used = 0
    # The same seed-then-spend shape again, a dict rather than a count: what
    # is left of the quantified pantry after every stage before this one. Each
    # stage reads it through `config["inventory_ledger"]` (injected in memory,
    # never written to disk — see `select_nudge_foods` for the same channel),
    # so a later meal type is told what an earlier one actually used rather
    # than being handed the full pantry and writing the same tin into a fourth
    # recipe. Empty when nothing in the list carries a quantity, which leaves
    # `inventory_instruction` emitting exactly the line it always did.
    inventory_ledger = seed_inventory_ledger(config)

    by_id = spec.by_id()
    # What the meals this run actually generates may spend: the day's target
    # less whatever a skipped-but-eaten slot already claims (dinner out, a
    # working lunch — `SlotSpec.skip_estimate`). Floored at 0 for the same
    # reason `split_targets` floors an overspent `meal_overrides`: a day whose
    # estimate exceeds its target should land visibly at zero rather than hand
    # the remaining meals a negative budget to scale against.
    #
    # `targets` itself is deliberately left whole — it becomes `WeekPlan.
    # targets`, the telemetry header's denominator, and the day's goal doesn't
    # shrink because part of it was met at a restaurant. `day_slot_macros`
    # adds the same estimate to the numerator, so the two agree.
    plannable_targets: Dict[str, dict] = {}
    for day in spec.days:
        spent = skip_estimate_totals(spec.slots, day)
        plannable_targets[day] = {
            key: max(0.0, float(targets[day][key]) - spent[key]) for key in MACRO_KEYS
        }
        if note_callback and any(spent[key] for key in MACRO_KEYS):
            note_callback(
                f"{day}: {spent['calories']:.0f} kcal / {spent['protein_g']:.0f}g protein "
                "reserved for a meal eaten out — planning the rest of the day around it"
            )

    # The daily targets, mutated down as each meal type's actual consumption
    # is subtracted — see the cascade step at the end of the loop below. This
    # is the "daily_macro_budgets" the horizontal-generation design cascades
    # through.
    daily_macro_budgets: Dict[str, dict] = {
        day: dict(plannable_targets[day]) for day in spec.days
    }

    events: Dict[str, CookEvent] = {}
    failures: Dict[str, str] = {}

    order = meal_type_order(config)

    # What each meal would be briefed had nothing drifted: `split_targets`
    # against the day's *full* target, with every slot pending. Identical to
    # what the first stage below computes for itself (stage 0's pending set is
    # every meal type, and the day is still unmutated), but needed at every
    # later stage as the ceiling `cap_to_weighted_share` measures against —
    # see it for why the last stage otherwise inherits the whole week's drift.
    apriori_budgets: Dict[str, Dict[str, dict]] = {}
    for day in spec.days:
        day_slots = [
            by_id[slot_id(day, meal_type)]
            for meal_type in order
            if by_id[slot_id(day, meal_type)].mode in (MODE_COOK, MODE_LEFTOVER)
        ]
        apriori_budgets[day] = split_targets(
            plannable_targets[day], day_slots, day_multiplicity(spec, day), config,
            meal_overrides_for(day, config),
            # The day's *whole* fibre target, and the only place this run
            # computes one. Every stage below reads its slot's share back out
            # of here rather than splitting again, which is what makes a
            # meal's fibre brief the same number whichever stage generates it
            # — see the note at the stage loop for why fibre must not cascade.
            #
            # Not reduced by `skip_estimate_totals`, unlike the macros above:
            # a skip estimate carries the budgeted four alone, because the
            # fibre in a meal nobody cooked isn't estimable and 0 is more
            # honest than a guess. So a skipped-but-eaten slot leaves the
            # day's fibre goal whole and simply isn't in the denominator that
            # divides it, which is the same direction `targets` itself takes:
            # the day's goal doesn't shrink because part of it was met at a
            # restaurant.
            fiber_target_g=float(targets[day].get("fiber_g") or 0.0),
        )
    for stage_index, meal_type in enumerate(order):
        # Every day this meal type is cooked, split by who cooks it. A slot
        # carrying a pinned favourite still needs a budget computed (it is
        # trimmed onto it, and its actual output still cascades), so it stays
        # in `all_cook_days` — it is only kept out of the *model* call.
        all_cook_days = [
            day for day in spec.days if by_id[slot_id(day, meal_type)].mode == MODE_COOK
        ]
        pinned_days = [
            day for day in all_cook_days if slot_id(day, meal_type) in pinned_recipes
        ]
        cook_days = [day for day in all_cook_days if day not in pinned_days]

        if progress_callback:
            progress_callback(meal_type, len(cook_days))

        # Publish what is left of the pantry to the stage about to run.
        # In-memory only, the channel `nudge_foods` already uses — and the
        # reason `save_config_keys` merges named keys rather than saving the
        # config it is handed: this one must never reach disk, since a count
        # decremented by a run that has not happened yet would be a config
        # file describing a fridge nobody has opened.
        config["inventory_ledger"] = dict(inventory_ledger)

        if all_cook_days:
            # This stage's per-day budget: split what's left of each day
            # (already reduced by every earlier-generated meal type) across
            # whichever meal types haven't been resolved yet — current stage
            # plus everything later in `order` — the same weight/override
            # logic `split_targets` already applies within one day, just
            # recomputed fresh at each stage against a shrinking remainder.
            pending_types = order[stage_index:]
            day_budgets: Dict[str, dict] = {}
            # Days whose budget came from a `meal_overrides` entry — a
            # different kind of pin from `pinned_days` above, which is about a
            # pinned *recipe*. Passed to the model so it knows the number is
            # fixed rather than a share of a remainder.
            fixed_budget_days: List[str] = []
            for day in all_cook_days:
                # A not-yet-resolved LEFTOVER slot (source not generated this
                # run yet — always true for a pending meal type, since its
                # source is either the same pending meal type or resolves no
                # earlier than it) still has to claim a weighted share of the
                # day, or the cook slots below would spend budget it needs
                # once its own stage runs. split_targets doesn't generate it
                # — nothing downstream reads a leftover slot's own budget
                # entry — it only uses it to shrink what's left for the rest.
                pending_slots = [
                    by_id[slot_id(day, pending_type)]
                    for pending_type in pending_types
                    if by_id[slot_id(day, pending_type)].mode in (MODE_COOK, MODE_LEFTOVER)
                ]
                overrides = meal_overrides_for(day, config)
                budgets = split_targets(
                    daily_macro_budgets[day], pending_slots, day_multiplicity(spec, day),
                    config, overrides,
                )
                this_slot = slot_id(day, meal_type)
                budget = budgets[this_slot]
                if meal_type in overrides:
                    # A pinned budget is a fixed number by definition — it was
                    # assigned verbatim by `split_targets` and never took a
                    # share of the remainder, so there is no drift on it to cap.
                    fixed_budget_days.append(day)
                else:
                    budget, capped = cap_to_weighted_share(
                        budget, apriori_budgets[day].get(this_slot), config
                    )
                    if capped:
                        logger.info(
                            "%s %s: budget capped to %.0f kcal — earlier meals came back "
                            "under brief and the remainder would have pushed this slot "
                            "past %sx its share; the day will land under target",
                            day, meal_type, budget["calories"],
                            planning_rule(config, "max_meal_share_multiple"),
                        )
                        if note_callback:
                            note_callback(
                                f"{day} {meal_type}: capped at {budget['calories']:.0f} kcal "
                                "rather than absorbing the day's whole shortfall"
                            )
                # **Fibre does not cascade, and this is where it is kept out.**
                # Every macro above is a share of what is *left* of the day
                # after each earlier stage's actual output, because the day's
                # energy has to total. A fibre target is a goal to reach rather
                # than a sum to spend, and models come back fibre-light far
                # more often than fibre-heavy — so cascading it would pile the
                # week's whole shortfall onto whichever meal type is generated
                # last, which is precisely the failure `cap_to_weighted_share`
                # exists to bound for calories and cannot bound here (a portion
                # trim scales fibre with everything else and can no more add
                # fibre than it can add protein).
                #
                # So the share comes from `apriori_budgets` — the day's full
                # target, split once before any stage ran — and a meal that
                # came back short simply leaves the day short, visibly, in the
                # telemetry header's `FIB 24/30g`. Same standing answer as an
                # orphaned leftover and a capped surplus: show the gap, do not
                # distort a meal to hide it.
                fibre_share = (apriori_budgets[day].get(this_slot) or {}).get("fiber_g")
                if fibre_share:
                    budget = dict(budget, fiber_g=fibre_share)
                day_budgets[day] = budget

            carried_descriptions_by_day = {
                day: carried_descriptions(spec, day, events) for day in cook_days
            }

            # Pinned favourites first, and outside the try: they cannot raise
            # a provider error, and building them before the model call means
            # a meal type whose every slot is pinned makes no API call at all.
            stage_events: Dict[str, CookEvent] = {}
            for day in pinned_days:
                pinned_slot = by_id[slot_id(day, meal_type)]
                # Trimmed onto its budget exactly as a generated recipe is —
                # a favourite was saved at whatever portion it was cooked at,
                # and serving it unscaled would spend a budget the rest of the
                # day is counting on. `fit_recipe_to_budget` clamps to
                # `portion_trim_limits`, so a favourite too far off simply
                # lands as close as the clamp allows and shows as a delta.
                fitted, factor = fit_recipe_to_budget(
                    single_serving(pinned_recipes[pinned_slot.id]),
                    day_budgets[day],
                    config,
                )
                if note_callback and abs(factor - 1.0) > 0.01:
                    note_callback(
                        f"{day} {meal_type}: saved favourite "
                        f"\"{fitted.name}\" scaled to {factor:.0%} of its "
                        "saved portion to fit the day's budget"
                    )
                stage_events[pinned_slot.id] = build_cook_event(
                    pinned_slot, fitted, spec, portions, claims, config
                )

            try:
                # Only the unpinned days: `_generate_meal_type_events` derives
                # which slots to ask for from `day_budgets`' own keys, so
                # handing it the full dict would put a pinned favourite's day
                # back in the prompt and get a second recipe generated for a
                # slot already filled.
                # `avoid_proteins` is extended from `stage_events` only after
                # this stage finishes, so the pins built moments ago are not in
                # it yet — and until dinner favourites existed that was
                # harmless, because no meal type could pin and generate at the
                # same time in a way variety cared about. It is not harmless
                # now: `DINNER_VARIETY_RULE` promises the same primary protein
                # never lands on two consecutive nights, and a pinned lamb
                # Thursday that the model cannot see is exactly how a
                # generated lamb Friday gets through. Appended rather than
                # folded into the running list, which stays the record of what
                # *completed* stages actually cooked.
                pinned_proteins = [
                    protein
                    for protein in (
                        extract_main_protein(event.recipe)
                        for event in stage_events.values()
                    )
                    if protein
                ]
                model_events = await _generate_meal_type_events(
                    meal_type, spec, config,
                    {day: day_budgets[day] for day in cook_days},
                    portions, claims,
                    carried_descriptions_by_day,
                    [day for day in fixed_budget_days if day in cook_days],
                    avoid_proteins[-protein_avoid_window:] + pinned_proteins,
                    avoid_recipe_names + [e.recipe.name for e in stage_events.values()],
                    note_callback, seafood_used,
                ) if cook_days else {}
                stage_events.update(model_events)
            except Exception as exc:
                # One bad meal type must not discard everything else. Free
                # routes fail in ways no amount of retrying fixes (a provider
                # returning an empty completion, a model that can't hit the
                # budget), and losing one meal type must not cost the other
                # three. Every day this stage would have cooked is recorded
                # and skipped; those slots render as "not generated" and
                # their ingredients never reach a shopping list.
                message = short_error(exc)
                for day in cook_days:
                    failures[slot_id(day, meal_type)] = message
                logger.warning(
                    "%s: generation failed for %s — %s", meal_type, ", ".join(cook_days), message
                )
                if note_callback:
                    note_callback(f"{meal_type}: generation failed — {message}")
                # `stage_events` keeps whatever the pinned favourites already
                # produced: those never went near the provider, so a failed
                # call must not discard them — the same "one failure must not
                # cost what already succeeded" rule as the per-meal-type catch
                # itself.

            spent = spend_inventory(inventory_ledger, stage_events.values())
            if note_callback:
                for name, grams in sorted(spent.items()):
                    remaining = inventory_ledger[name]
                    note_callback(
                        f"{meal_type}: used {grams:.0f}g of {name} from the pantry"
                        + (
                            f", {remaining:.0f}g left"
                            if remaining > 0
                            else " — none left for the later meals"
                        )
                    )

            for event in stage_events.values():
                protein = extract_main_protein(event.recipe)
                if protein and protein not in avoid_proteins:
                    avoid_proteins.append(protein)
                if event.recipe.name not in avoid_recipe_names:
                    avoid_recipe_names.append(event.recipe.name)
                # Counted per cook event, not per slot that eats it: a bulk-
                # cooked salmon feeding three lunches was bought once and is
                # one trip to the fish counter, which is what the cap is about.
                if is_seafood_meal(event.recipe):
                    seafood_used += 1
            events.update(stage_events)

        # Cascade: subtract this meal type's actual per-day consumption from
        # daily_macro_budgets, so the NEXT meal type's split (above) sees the
        # real remaining number. A day whose cook just failed is left
        # untouched — nothing was actually eaten, so nothing is owed.
        for day in spec.days:
            slot = by_id[slot_id(day, meal_type)]
            if slot.mode == MODE_COOK:
                event = events.get(slot.id)
                if event is None:
                    continue
                times = day_multiplicity(spec, day).get(slot.id, 1)
                serving = event.recipe.per_serving_macros
                for key in MACRO_KEYS:
                    daily_macro_budgets[day][key] = max(
                        0.0, daily_macro_budgets[day][key] - serving[key] * times
                    )
            elif slot.mode == MODE_LEFTOVER and slot.source:
                source = by_id.get(slot.source)
                if source is None or source.day == day:
                    # A same-day source's consumption was already folded into
                    # the cook's own subtraction above, via day_multiplicity.
                    continue
                event = events.get(slot.source)
                if event is None:
                    continue
                serving = event.recipe.per_serving_macros
                for key in MACRO_KEYS:
                    daily_macro_budgets[day][key] = max(
                        0.0, daily_macro_budgets[day][key] - serving[key]
                    )

    ordered_events = [events[slot.id] for slot in spec.cook_slots() if slot.id in events]

    # A failed prep session must not fail the week either — same rule as a
    # failed day (see CLAUDE.md), and for the same reason: this runs after
    # every day has already succeeded, so losing the whole plan to one extra
    # call would be the worst possible outcome after a full run.
    sunday_prep_session = None
    try:
        sunday_prep_session = await asyncio.to_thread(
            generate_sunday_prep_session, ordered_events, spec, config
        )
        if sunday_prep_session and note_callback:
            note_callback(
                f"Sunday prep session: {sunday_prep_session.total_active_minutes} active "
                f"min across {len(sunday_prep_session.timeline)} phase(s)"
            )
    except Exception as exc:
        message = short_error(exc)
        logger.warning("sunday_prep: generation failed — %s", message)
        if note_callback:
            note_callback(f"Sunday prep session generation failed — {message}")

    generated_at = datetime.now().isoformat()
    plan = WeekPlan(
        days=spec.days,
        servings_per_meal=spec.servings_per_meal,
        generated_at=generated_at,
        # Anchored on the same `generated_at`, not a fresh `date.today()`
        # call, so the two can't disagree about which instant "now" was.
        week_start_date=week_date_range(spec.days, generated_at)[0].isoformat(),
        cook_events=ordered_events,
        slots=spec.slots,
        targets=targets,
        failures=failures,
        sunday_prep_session=sunday_prep_session,
        unique_plants=collect_unique_plants(ordered_events),
    )

    # The food-safety backstop, run a second time now the dishes are real.
    # `validate_week` already ran it over the bare grid, where every cook
    # could only be judged against the default window because no recipe
    # existed yet; this is where a dropped `storage_class`, a rice dish that
    # slipped past `reject_short_storage_class`, or a pre-existing catalog
    # favourite pinned into a long-span slot is finally visible.
    #
    # Reported, never corrected. It cannot raise — this runs after every stage
    # has already succeeded, and losing a 20-minute week to a note is the
    # worst possible outcome (the same rule "a failed meal must not fail the
    # week" states) — and it cannot silently re-point a leftover either,
    # because a plan quietly rewritten is one nobody checks.
    for message in storage_safety_errors(
        spec, config, {event.slot_id: event.recipe.storage_class for event in ordered_events}
    ):
        logger.warning("storage: %s", message)
        if note_callback:
            note_callback(f"Food safety: {message}")

    # The cascade above cannot reconcile a day whose every slot is pinned,
    # skipped or fed by another day's cook — there is no flexible slot left
    # to absorb the gap between what a leftover slot's weighted share
    # reserved and what its source recipe actually delivers. Same policy as
    # `cap_to_weighted_share`/`apply_protein_floor`: land under target and say
    # so, rather than silently swallowing the shortfall. Reuses
    # `day_slot_macros` — the same walk the telemetry header renders — so this
    # can never disagree with what the UI shows for the same day.
    for day in plan.days:
        goal = targets[day]["calories"]
        if goal <= 0:
            continue
        planned = plan.day_slot_macros(day)["calories"]
        if planned < goal * UNDER_TARGET_NOTE_THRESHOLD:
            logger.warning(
                "%s plans %.0f of %.0f kcal — no flexible slot to absorb the "
                "difference", day, planned, goal,
            )
            if note_callback:
                note_callback(
                    f"{day}: only {planned:.0f} of {goal:.0f} kcal is planned — "
                    "its remaining meals are skipped, pinned or leftovers"
                )

    return plan


async def regenerate_single_day(
    day: str,
    spec: WeekSpec,
    config: dict,
    week_plan: WeekPlan,
    history: Optional[List[dict]] = None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Re-cook just `day`, leaving every other day's cook events untouched.

    Every other day's cook events already live in `week_plan` — a leftover
    slot on `day` only ever points at an *earlier* day, so its source is
    already resolved and doesn't need regenerating; a later day's leftover
    that points *at* `day` keeps pointing at the same slot_id, so it picks up
    the new recipe automatically once that slot_id is replaced below. Neither
    direction needs the rest of the week to be walked again — that's the
    whole difference from `generate_week_plan`.
    """
    store = repository or LocalJSONRepository()
    if history is None:
        history = await store.load_history()
    # Same hydration the full run does, so a re-cooked day aims at the same
    # target as the days around it rather than reverting to the file's.
    config = await hydrate_config(config, repository, note_callback)
    rejections = await store.load_rejections()
    if rejections:
        config = dict(config, rejected_preferences=rejections)
    # Same spans the full run computes, from the same function: a re-cooked
    # slot still has to feed everything that claims it, and a replacement dish
    # that keeps for less than the original would be a safety regression
    # introduced by a fix. Unlike the seafood cap and the pantry ledger — both
    # deliberately omitted here because a single replaced meal has no week in
    # front of it to count against — this is a property of the slot, not of
    # the week, so it applies unchanged.
    config = dict(config, storage_spans=storage_spans(spec, config))
    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    by_slot = dict(week_plan.by_slot())

    # Seeded from history, same as a full week, then extended with every
    # OTHER day's proteins already locked into this plan — `day`'s own
    # (about to be replaced) proteins must not suppress themselves on retry.
    avoid_proteins = recent_main_proteins(history, config)
    # Same idea for recipe names — history's 4-week window plus every OTHER
    # day already locked into this plan; `day`'s own (about to be replaced)
    # recipes must not suppress themselves on retry.
    avoid_recipe_names = recent_recipe_names(history)
    for event in week_plan.cook_events:
        if event.day == day:
            continue
        protein = extract_main_protein(event.recipe)
        if protein and protein not in avoid_proteins:
            avoid_proteins.append(protein)
        if event.recipe.name not in avoid_recipe_names:
            avoid_recipe_names.append(event.recipe.name)

    # Keyed by slot_id, not day — see generate_week_plan, which now records a
    # failure per (day, meal_type) since a single API call no longer always
    # covers the whole day. A day-level regeneration still fails atomically
    # (one call, every cook slot on the day), so every one of them gets the
    # same message.
    cook_slots_today = spec.cook_slots_on(day)
    failures = dict(week_plan.failures)
    try:
        day_events = await _generate_day_events(
            day, spec, config, targets[day], portions, claims, by_slot,
            avoid_proteins, avoid_recipe_names, note_callback,
        )
    except Exception as exc:
        message = short_error(exc)
        for slot in cook_slots_today:
            failures[slot.id] = message
        logger.warning("%s: regeneration failed — %s", day, message)
        if note_callback:
            note_callback(f"{day}: regeneration failed — {message}")
        return week_plan.model_copy(update={"failures": failures})

    by_slot.update(day_events)
    for slot in cook_slots_today:
        failures.pop(slot.id, None)
    ordered_events = [by_slot[slot.id] for slot in spec.cook_slots() if slot.id in by_slot]

    # A saved Sunday prep session names specific recipes/ingredients from the
    # OLD plan. If `day` contributed a batch cook either before or after this
    # regeneration, that session may now describe a recipe that no longer
    # exists — a stale prep plan is worse than none, so drop it rather than
    # let the timeline silently disagree with the new recipes. It is not
    # regenerated here: this call is one targeted retry, not a second
    # sunday_prep API call on top of it.
    sunday_prep_session = week_plan.sunday_prep_session
    if sunday_prep_session is not None:
        was_candidate = any(
            event.day == day and is_sunday_prepped(event, week_plan)
            for event in week_plan.cook_events
        )
        now_candidate = any(event.recipe.prep_notes for event in day_events.values())
        if was_candidate or now_candidate:
            sunday_prep_session = None

    return week_plan.model_copy(
        update={
            "generated_at": datetime.now().isoformat(),
            "cook_events": ordered_events,
            "slots": spec.slots,
            "targets": targets,
            "failures": failures,
            "sunday_prep_session": sunday_prep_session,
            "unique_plants": collect_unique_plants(ordered_events),
        }
    )


async def regenerate_single_meal(
    slot_id: str,
    spec: WeekSpec,
    config: dict,
    week_plan: WeekPlan,
    history: Optional[List[dict]] = None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Re-cook just one meal, leaving every other slot's cook event untouched.

    The narrowest-grained regeneration in the app — `regenerate_single_day`
    still re-splits and re-generates every cook on that day, which is overkill
    for "just redo Tuesday dinner, the rest of the day is fine." Here every
    OTHER slot on `day` (leftover or independently cooked) is treated as
    fixed: its already-locked-in per-serving macros are summed and subtracted
    from the day's target, and whatever budget is left over goes entirely to
    this one meal — divided by how many times it's eaten today, the same rule
    `split_targets` applies to any other flexible slot. One API call, one
    recipe.

    Unlike `carried_macros` (which only knows about leftovers, because a
    full-day generation produces every same-day cook together in one call),
    this also has to treat a sibling COOK slot on the same day as fixed — it
    already has a recipe in `week_plan` that isn't being touched.

    **When the day being re-cooked is today and Cronometer has logged it, the
    log replaces the plan for the meals already behind you.** Regenerating
    tonight's dinner at 5pm is the one case where the app knows what was really
    eaten rather than what was meant to be: `logged_intake_for` supplies the
    measured total, and only the *later* slots keep their planned reservation
    (ordered by `MEAL_TIME_OF_DAY`, the same table the training rules use).
    The model is then briefed on the genuine remaining deficit — which is
    usually not the planned one, because a 2200 kcal day with 1600 already
    logged leaves a different dinner than the plan assumed.
    """
    day, _ = parse_slot_id(slot_id)
    slot = spec.by_id().get(slot_id)
    if slot is None or slot.mode != MODE_COOK:
        raise ValueError(f"{slot_label(slot_id)} isn't a cooked meal — nothing to regenerate.")

    store = repository or LocalJSONRepository()
    if history is None:
        history = await store.load_history()
    logged = logged_intake_for(day, await store.load_biometrics())
    config = await hydrate_config(config, store, note_callback)
    rejections = await store.load_rejections()
    if rejections:
        config = dict(config, rejected_preferences=rejections)

    # Same spans the full run computes, from the same function: a re-cooked
    # slot still has to feed everything that claims it, and a replacement dish
    # that keeps for less than the original would be a safety regression
    # introduced by a fix. Unlike the seafood cap and the pantry ledger — both
    # deliberately omitted here because a single replaced meal has no week in
    # front of it to count against — this is a property of the slot, not of
    # the week, so it applies unchanged.
    config = dict(config, storage_spans=storage_spans(spec, config))
    targets = week_targets(spec, config)
    day_target = targets[day]
    portions = portions_for(spec)
    claims = eaten_on(spec)
    by_slot = dict(week_plan.by_slot())
    # Captured before `by_slot[slot_id]` is overwritten below with the new
    # event, so the sunday_prep_session check further down can ask what this
    # slot used to hold without a second `week_plan.by_slot()` rebuild.
    old_event = by_slot.get(slot_id)
    multiplicity = day_multiplicity(spec, day)

    other: List[Tuple[SlotSpec, CookEvent]] = []
    for other_slot in spec.slots:
        if other_slot.day != day or other_slot.id == slot_id or other_slot.mode == MODE_SKIP:
            continue
        source_id = other_slot.id if other_slot.mode == MODE_COOK else other_slot.source
        if source_id == slot_id:
            # A same-day leftover of THIS meal's own batch — its share is
            # already covered by dividing this meal's budget by multiplicity
            # below, not a separate fixed amount to subtract.
            continue
        event = by_slot.get(source_id)
        if event is None:
            continue
        other.append((other_slot, event))

    if logged is not None:
        # The log already contains whatever was eaten earlier today, so those
        # siblings' *planned* macros must not be subtracted a second time —
        # double-counting breakfast would shrink dinner by a whole meal. Slots
        # later in the day are still ahead of you and keep their reservation.
        this_meal_at = clock_minutes(MEAL_TIME_OF_DAY.get(slot.meal_type, "12:00"))
        other = [
            pair
            for pair in other
            if clock_minutes(MEAL_TIME_OF_DAY.get(pair[0].meal_type, "12:00")) > this_meal_at
        ]

    other_totals = sum_serving_macros(event for _, event in other)
    other_descriptions: List[str] = []
    if logged is not None:
        for key in MACRO_KEYS:
            other_totals[key] += logged[key]
        other_descriptions.append(
            f"ALREADY EATEN TODAY (logged from Cronometer, not a recipe to "
            f"reproduce): {logged['calories']:.0f} kcal, "
            f"{logged['protein_g']:.0f}g protein, {logged['net_carbs_g']:.0f}g net carbs, "
            f"{logged['fat_g']:.0f}g fat"
        )
        if note_callback:
            note_callback(
                f"{slot_label(slot_id)}: {logged['calories']:.0f} kcal already logged today — "
                "briefing the model on the remaining deficit only"
            )
    for other_slot, event in other:
        serving = event.recipe.per_serving_macros
        origin = (
            f'leftovers of "{event.recipe.name}" (cooked {event.day})'
            if other_slot.mode == MODE_LEFTOVER
            else f'"{event.recipe.name}" (already generated)'
        )
        other_descriptions.append(
            f"{other_slot.meal_type}: {origin} — {serving['calories']:.0f} kcal, "
            f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
            f"{serving['fat_g']:.0f}g fat"
        )

    # Seeded from history, same as regenerate_single_day, then extended with
    # every OTHER slot already locked into this plan — this meal's own
    # (about to be replaced) protein/name must not suppress itself on retry.
    avoid_proteins = recent_main_proteins(history, config)
    avoid_recipe_names = recent_recipe_names(history)
    for event in week_plan.cook_events:
        if event.slot_id == slot_id:
            continue
        protein = extract_main_protein(event.recipe)
        if protein and protein not in avoid_proteins:
            avoid_proteins.append(protein)
        if event.recipe.name not in avoid_recipe_names:
            avoid_recipe_names.append(event.recipe.name)

    protein_avoid_window = planning_rule(config, "protein_avoid_window")
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_day,
        day=day,
        targets=day_target,
        cook_slots=[slot],
        config=config,
        servings_per_meal=spec.servings_per_meal,
        multiplicity=multiplicity,
        carried=other_totals,
        carried_descriptions=other_descriptions,
        avoid_proteins=avoid_proteins[-protein_avoid_window:],
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    new_event = build_cook_event(
        slot, recipes[slot.meal_type], spec, portions, claims, config
    )
    by_slot[slot_id] = new_event
    ordered_events = [by_slot[s.id] for s in spec.cook_slots() if s.id in by_slot]

    # Same rule as regenerate_single_day: a saved Sunday prep session names
    # specific recipes from the OLD plan, and this meal may have joined or
    # left the batch-prep candidate set — drop rather than risk a stale plan.
    sunday_prep_session = week_plan.sunday_prep_session
    if sunday_prep_session is not None:
        was_candidate = bool(old_event and is_sunday_prepped(old_event, week_plan))
        now_candidate = bool(new_event.recipe.prep_notes)
        if was_candidate or now_candidate:
            sunday_prep_session = None

    # This slot just cooked successfully, so any failure recorded against it by
    # an earlier run is stale — same clearing `regenerate_single_day` does for
    # every slot it re-cooks. Without it the card turns green while
    # `WeekPlan.failures` still names the meal, and the drawer's failure list
    # and the shopping drawer's "nothing for those meals is on this list" note
    # both keep reporting a meal that now exists. The per-card regenerate
    # button is offered *on* NOT GENERATED cards, so this is the common path,
    # not an edge case.
    failures = dict(week_plan.failures)
    failures.pop(slot_id, None)

    return week_plan.model_copy(
        update={
            "generated_at": datetime.now().isoformat(),
            "cook_events": ordered_events,
            "slots": spec.slots,
            "targets": targets,
            "failures": failures,
            "sunday_prep_session": sunday_prep_session,
            "unique_plants": collect_unique_plants(ordered_events),
        }
    )


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def is_seafood_meal(recipe: Recipe) -> bool:
    """Does this recipe's *dominant* protein come from the sea?

    What `sourcing.max_seafood_meals_per_week` is counted in. Keyed off the
    highest-protein ingredient — the same cheap proxy `extract_main_protein`
    uses — rather than a scan of every ingredient name, because a scan spends
    the week's whole seafood allowance on a Thai dinner's tablespoon of fish
    sauce or a bowl of dashi. Unlike `extract_main_protein` this applies to
    every meal type: a smoked-salmon breakfast is a seafood meal, and a cap
    that only watched lunch and dinner would miss it.
    """
    if not recipe.ingredients:
        return False
    main = max(recipe.ingredients, key=lambda ingredient: ingredient.protein_g)
    name = main.name.lower()
    return any(term in name for term in SEAFOOD_TERMS)


def extract_main_protein(recipe: Recipe) -> Optional[str]:
    """Cheap proxy for a recipe's protein source: its highest-protein
    ingredient. Only meaningful for lunch/dinner, where one dominates."""
    if recipe.meal_type.lower() not in ("lunch", "dinner") or not recipe.ingredients:
        return None
    return max(recipe.ingredients, key=lambda ingredient: ingredient.protein_g).name


async def record_week_history(
    week_plan: WeekPlan,
    repository: Optional[PlanRepository] = None,
    config: Optional[dict] = None,
    days: Optional[List[str]] = None,
) -> None:
    """One history entry per cooked day, so rotation carries across weeks.

    `config` supplies `planning_rules.history_max_entries`; omitted (or
    missing the key) falls back to DEFAULT_PLANNING_RULES's value, same as
    every other planning_rule() read.

    `days` restricts which of `week_plan.days` get a new entry — defaults to
    all of them (a full week's worth). `regenerate_single_day` passes just
    the one day it touched; recording the whole plan there would re-append
    history for six days that were never regenerated, throwing off rotation
    for styles/cuisines/proteins that had nothing to do with this run.
    """
    max_entries = planning_rule(config, "history_max_entries")
    repository = repository or LocalJSONRepository()
    history = await repository.load_history()
    generated_at = week_plan.generated_at
    target_days = week_plan.days if days is None else days

    for day in target_days:
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
                # None on a plan generated before week_start_date existed —
                # there is no plausible-looking anchor worth guessing for a
                # *past* day, unlike today_in_week's live "does this cover
                # today" check. day_date needs the real start date or none
                # at all.
                "date": (
                    day_date(week_plan.week_start_date, week_plan.days, day)
                    if week_plan.week_start_date
                    else None
                ),
                "day_of_week": day,
                "generated_at": generated_at,
                "cuisine": next((event.cuisine for event in events if event.cuisine), None),
                "styles": {event.meal_type: event.style for event in events if event.style},
                "main_proteins": proteins,
                "recipe_names": [event.recipe.name for event in events],
                # The macro budget this day was actually planned against —
                # week_plan.targets is the same dict state.targets_for reads
                # live, archived here because it's overwritten on every new
                # generation and a "planned vs. actual" comparison for a past
                # day needs it kept somewhere that survives the next run.
                "targets": (
                    dict(week_plan.targets[day]) if day in week_plan.targets else None
                ),
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
        print("\n!! Some meals failed to generate — re-run to retry them:")
        for key, error in week_plan.failures.items():
            print(f"   {slot_label(key)}: {error}")
    by_slot = week_plan.by_slot()
    slots_by_day: Dict[str, List[SlotSpec]] = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        totals = week_plan.day_slot_macros(day)
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

    session = week_plan.sunday_prep_session
    if session:
        print(
            f"\nSunday Prep Session ({session.total_active_minutes} active / "
            f"{session.total_passive_minutes} passive min)"
        )
        print("=" * 26)
        for phase in session.timeline:
            print(f"  {phase.name} — {phase.active_minutes} active / {phase.passive_minutes} passive min")
            if phase.description:
                print(f"    {phase.description}")
        if session.aggregated_ingredients:
            print("  Aggregated prep:")
            for item, note in session.aggregated_ingredients.items():
                print(f"    {item}: {note}")


def window_shopping_list(
    week_plan: WeekPlan, window: ShoppingWindow, config: Optional[dict] = None
) -> ShoppingList:
    """One trip's list, with what is already in the house taken off it.

    The single call the CLI's printed list and its saved Markdown both make,
    so the two cannot disagree about a trip. `config` is optional because a
    caller without one wants the list the recipes need — see
    `shopping.apply_pantry`, which is where the render-time-never-stored
    argument lives.
    """
    shopping_list = aggregate_cook_events(
        week_plan.events_on_days(window.days), window.days
    )
    if config is None:
        return shopping_list
    return apply_pantry(shopping_list, inventory_entries(config))


def print_shopping_windows(
    week_plan: WeekPlan, windows: List[ShoppingWindow], config: Optional[dict] = None
) -> None:
    for window in windows:
        events = week_plan.events_on_days(window.days)
        print(f"\n{window.label}")
        print("=" * len(window.label))
        if not events:
            print("  (nothing cooked in this window)")
            continue
        shopping_list = window_shopping_list(week_plan, window, config)
        print(format_shopping_list_text(shopping_list, cook_events=events))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Weekly Meal Planner CLI")
    parser.add_argument(
        "--config-dir",
        default=DEFAULT_STORAGE_PATHS.config_dir,
        help="Directory holding the config JSON files (profile, meals, week, schedule, engine)",
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
            f"Load the week from {DEFAULT_STORAGE_PATHS.week_plan} instead of calling "
            "OpenRouter (for iterating on the shopping list without API calls)."
        ),
    )
    return parser.parse_args(argv)


class WeekNotValidError(ValueError):
    """`validate_week` rejected the grid before a single API call was paid for.

    Its own class rather than a bare `ValueError` because the two callers of
    `generate_and_store_week` do genuinely different things with it: the CLI
    prints the list and exits 1, and the API route records it on the job under
    its own key, so a client can tell "your config is wrong" from "the provider
    fell over" without parsing a message. `errors` is the list `validate_week`
    returned, verbatim.
    """

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


async def generate_and_store_week(
    repository: PlanRepository,
    config: Optional[dict] = None,
    *,
    week_identifier: str = "current",
    week_start: Optional[str] = None,
    servings: Optional[int] = None,
    spec_transform=None,
    on_ready=None,
    progress_callback=None,
    note_callback=None,
) -> WeekPlan:
    """Build a fresh week from config, generate it, store it, record history.

    The whole sequence a front end that holds no session state performs: the
    CLI (`run_cli`) and the API's generation job (`api.py`) are both exactly
    this, and they call it rather than each spelling it out, because the order
    here carries three decisions that would drift silently if copied —
    `apply_training_adjustments` before the grid is built (so `week_targets`
    and `meal_overrides_for` see one adjusted config), `resolve_auto_choices`
    before `validate_week` (so the pass checks the grid actually generated
    from), and `save_week_plan` before `record_week_history` (so history can
    never describe a week that isn't on disk).

    **This is deliberately not the UI's path**, and the difference is what
    `PlannerState` holds rather than an omission. `ui_generation.generate_week`
    starts from a *staged* spec carrying the user's own structural edits, so it
    has to clear the previous run's style/cuisine/pin/batch state off it first
    and apply the review dialog's batch toggles; a grid built fresh by
    `default_week_spec` has none of that on it, exactly as the CLI has always
    assumed. Anything that belongs to *every* generation goes here, where all
    three front ends get it; anything that is a session concept stays there.

    `spec_transform` and `on_ready` are the two seams the CLI needs and are
    both optional: `--leftover-lunches` reshapes the grid between building and
    resolving it, and the "Generating N-day plan…" line wants the resolved
    spec and the model name after validation but before the first call.
    """
    if config is None:
        config = await load_config_with_models(repository)
    config = apply_training_adjustments(config)

    spec = default_week_spec(config, week_start, servings)
    if spec_transform is not None:
        spec = spec_transform(spec)

    history = await repository.load_history()
    spec = resolve_auto_choices(spec, config, history)

    errors = validate_week(spec, config)
    if errors:
        raise WeekNotValidError(errors)

    if on_ready is not None:
        on_ready(spec, resolve_planner_model(config))

    week_plan = await generate_week_plan(
        spec,
        config,
        history,
        progress_callback=progress_callback,
        note_callback=note_callback,
        repository=repository,
    )

    await repository.save_week_plan(week_plan.model_dump(), week_identifier)
    await record_week_history(week_plan, repository, config)
    return week_plan


async def run_cli(args: argparse.Namespace, repository: PlanRepository) -> None:
    """The CLI's actual work, async so it can await the repository.

    Split from `main()` so there is exactly one `asyncio.run` in the process
    (in `main`) and everything below it is ordinary async code — the shape the
    future backend expects, and the reason storage calls are awaited here
    rather than bridged individually.
    """
    config = await load_config_with_models(repository)
    if args.model:
        config["openrouter_model"] = args.model

    if args.use_cached_plan:
        print(f"Loading cached week plan from {repository.paths.week_plan}...", flush=True)
        cached = await repository.load_week_plan()
        if cached is None:
            print(f"No cached week plan found ({repository.paths.week_plan}). Generate one first.")
            raise SystemExit(1)
        week_plan = WeekPlan.model_validate(cached)
        # The cached path skips `apply_training_adjustments`, which the
        # generation path applies inside `generate_and_store_week`. Nothing
        # below reads an adjusted key — `shop_days` comes from `shopping`,
        # which no adjustment touches — and re-deriving targets for a week
        # that was generated against its own would print a second set.
    else:
        def announce(spec: WeekSpec, model: str) -> None:
            """Fired after validation, before the first call — see `on_ready`."""
            cook_days = len({slot.day for slot in spec.cook_slots()})
            print(
                f"Generating {len(spec.days)}-day plan ({len(spec.cook_slots())} cooks "
                f"across {cook_days} days) using {model}...",
                flush=True,
            )

        # Named for what generate_week_plan actually passes: a meal type, not
        # a day. It used to say `day`, which printed "breakfast: leftovers
        # only" once generation moved to the meal-type axis.
        def report(meal_type: str, cooks: int) -> None:
            print(
                f"  {meal_type}: {cooks} recipe(s)..."
                if cooks
                else f"  {meal_type}: nothing to cook this week",
                flush=True,
            )

        leftover_lunches = None
        if args.leftover_lunches:
            from week import autofill_leftovers

            leftover_lunches = lambda spec: autofill_leftovers(spec, "lunch", "dinner")  # noqa: E731

        try:
            week_plan = await generate_and_store_week(
                repository,
                config,
                week_start=args.week_start,
                servings=args.servings,
                spec_transform=leftover_lunches,
                on_ready=announce,
                progress_callback=report,
                note_callback=lambda message: print(f"    {message}", flush=True),
            )
        except WeekNotValidError as invalid:
            print("Week plan is not valid:")
            for error in invalid.errors:
                print(f"  - {error}")
            raise SystemExit(1)

    print_week_summary(week_plan)

    shop_days = (
        [day.strip() for day in args.shop_days.split(",") if day.strip()]
        if args.shop_days
        else config["shopping"]["shop_days"]
    )
    windows = shopping_windows(week_plan.days, shop_days)

    print("\n\nShopping Lists")
    print("==============")
    print_shopping_windows(week_plan, windows, config)

    if args.save_shopping_list:
        sections = []
        for window in windows:
            events = week_plan.events_on_days(window.days)
            if not events:
                continue
            sections.append(
                format_shopping_list_markdown(
                    window_shopping_list(week_plan, window, config),
                    cook_events=events,
                    title=window.label,
                )
            )
        await repository.save_shopping_list("\n\n".join(sections))
        print(f"\nSaved shopping lists to {repository.paths.shopping_list}", flush=True)


def main() -> None:
    """Sync entry point: parse args, pick a repository, run the async CLI.

    `--config` still names a file because the only repository today is the
    local one; a backend implementation would be selected here instead and
    nothing below this line would change.
    """
    configure_logging()
    args = parse_args()
    repository = LocalJSONRepository(config_dir=args.config_dir)
    run_sync(run_cli(args, repository))


if __name__ == "__main__":
    main()
