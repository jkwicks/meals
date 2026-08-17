"""Tests for the portion-sizing layers in `src/planner.py`.

CLAUDE.md's "Portion sizing — three layers, because models can't size meals"
describes the design; this pins the one relationship in it that is easy to
break and expensive when broken.

Layer 2 (`fit_recipe_to_budget`) rescales a response linearly onto its budget,
clamped to `planning_rules.portion_trim_limits`. Layer 3
(`reject_untrimmable_macro_miss`) rejects a response so the model can retry —
and its threshold is *derived from layer 2's clamp*, not chosen independently.

That coupling is the whole point. An earlier version used a flat 25%
tolerance and a real 7-day run died on day 7: two responses at +62% and +43%
were rejected, the third attempt hit a provider bug, `max_retries` was
exhausted and the exception took the whole week with it. Both of those need
factors (0.62, 0.70) comfortably inside the clamp — the trim would have placed
them exactly on budget. A tolerance tighter than the trim's reach rejects
answers it could have fixed, and every rejection is another 30s-3min call.

So the invariant under test is: **anything the trim can rescue is accepted,
and only what it cannot is rejected.** `TestTheLayersAgree` asserts it
directly across the boundary rather than restating either threshold, so the
two cannot drift apart without a failure here.
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from planner import (  # noqa: E402
    DEFAULT_PLANNING_RULES,
    DayRecipes,
    Ingredient,
    MealTypeWeekRecipes,
    Recipe,
    fit_recipe_to_budget,
    planning_rule,
)

LOW, HIGH = DEFAULT_PLANNING_RULES["portion_trim_limits"]
DEADBAND = DEFAULT_PLANNING_RULES["portion_trim_deadband"]


def ingredient(calories: float, name: str = "Chicken breast") -> Ingredient:
    """One ingredient carrying `calories`, with macros in a plausible ratio.

    The exact macro split is irrelevant to every test here — layer 2 scales
    all four linearly and layer 3 only ever reads calories — but a recipe of
    pure calories and no protein would be a misleading fixture to read.
    """
    return Ingredient(
        name=name,
        quantity_g=max(1.0, calories / 1.65),
        nova_group=1,
        calories=calories,
        protein_g=calories * 0.075,
        net_carbs_g=calories * 0.02,
        fat_g=calories * 0.04,
    )


def recipe(calories: float, name: str = "Test dish", meal_type: str = "dinner") -> Recipe:
    return Recipe(
        name=name,
        meal_type=meal_type,
        ingredients=[ingredient(calories)],
        instructions=["Cook it."],
        prep_time_minutes=20,
    )


def budget(calories: float) -> dict:
    return {
        "calories": calories,
        "protein_g": calories * 0.075,
        "net_carbs_g": calories * 0.02,
        "fat_g": calories * 0.04,
    }


# A config permissive enough that the *ingredient* validators never fire, so
# these tests measure portion arithmetic alone. Writing these tests is what
# surfaced the context asymmetry `_dietary_rule` now fixes — see
# `test_diet_styles.TestIngredientRulesReadTheirContext`, which covers the
# four context shapes directly.
PERMISSIVE_CONFIG = {
    "planning_rules": dict(DEFAULT_PLANNING_RULES),
    "dietary_rules": {"allowed_nova_groups": [1, 2, 3], "banned_ingredients": []},
}


def ctx(day_budget: dict = None, config: dict = None, **extra) -> dict:
    """A validation context with only the keys actually being supplied."""
    context = dict(extra)
    if day_budget is not None:
        context["day_budget"] = day_budget
    if config is not None:
        context["config"] = config
    return context


class TestTrimToBudget(unittest.TestCase):
    """Layer 2 — linear rescale onto the budget."""

    def test_an_oversized_recipe_lands_on_its_budget(self):
        trimmed, factor = fit_recipe_to_budget(recipe(800), budget(600))
        self.assertAlmostEqual(factor, 0.75, places=6)
        self.assertAlmostEqual(trimmed.total_macros["calories"], 600, delta=1)

    def test_an_undersized_recipe_is_scaled_up(self):
        grown, factor = fit_recipe_to_budget(recipe(400), budget(500))
        self.assertAlmostEqual(factor, 1.25, places=6)
        self.assertAlmostEqual(grown.total_macros["calories"], 500, delta=1)

    def test_every_macro_scales_together(self):
        """A single factor resizes the portion without changing the dish.

        0.75 rather than a rounder 0.5, which is outside the 0.6 clamp floor
        and would be measuring the clamp instead of the scaling.
        """
        original = recipe(800)
        trimmed, factor = fit_recipe_to_budget(original, budget(600))
        self.assertAlmostEqual(factor, 0.75, places=6)
        for key in ("calories", "protein_g", "net_carbs_g", "fat_g"):
            with self.subTest(macro=key):
                self.assertAlmostEqual(
                    trimmed.total_macros[key], original.total_macros[key] * 0.75, delta=1
                )

    def test_a_near_miss_inside_the_deadband_is_left_alone(self):
        """Rewriting every quantity to chase 1% is churn, not correction."""
        _, factor = fit_recipe_to_budget(recipe(600), budget(600 * (1 + DEADBAND / 2)))
        self.assertEqual(factor, 1.0)

    def test_the_trim_is_clamped_at_both_ends(self):
        """The clamp is what stops a 30g breakfast or a 900g steak."""
        _, low_factor = fit_recipe_to_budget(recipe(2000), budget(200))
        self.assertAlmostEqual(low_factor, LOW, places=6)

        _, high_factor = fit_recipe_to_budget(recipe(100), budget(2000))
        self.assertAlmostEqual(high_factor, HIGH, places=6)

    def test_a_clamped_trim_does_not_reach_its_budget(self):
        """Deliberate: layer 3 is what stops this response ever arriving.

        Layer 2 never lies about what it can do — it clamps and reports the
        factor it actually used, leaving the residual visible as a delta in
        the day summary rather than distorting the portion to hide it.
        """
        trimmed, factor = fit_recipe_to_budget(recipe(2000), budget(200))
        self.assertEqual(factor, LOW)
        self.assertGreater(trimmed.total_macros["calories"], 200)

    def test_a_zero_budget_leaves_the_recipe_alone(self):
        _, factor = fit_recipe_to_budget(recipe(500), budget(0))
        self.assertEqual(factor, 1.0)


class TestRejectUntrimmableDay(unittest.TestCase):
    """Layer 3 on the single-day axis (`DayRecipes`)."""

    def validate(self, total_calories: float, budget_calories: float):
        return DayRecipes.model_validate(
            {"recipes": [recipe(total_calories).model_dump()]},
            context=ctx(day_budget=budget(budget_calories)),
        )

    def test_a_response_the_trim_can_rescue_is_accepted(self):
        # 1000 kcal against an 800 budget needs a 0.8 factor — well inside.
        self.assertIsNotNone(self.validate(1000, 800))

    def test_a_response_beyond_the_trims_reach_is_rejected(self):
        # 2000 against 800 needs 0.4, outside the 0.6 floor.
        with self.assertRaises(ValidationError) as caught:
            self.validate(2000, 800)
        self.assertIn("2000", str(caught.exception))

    def test_the_rejection_names_the_budget_and_the_overshoot(self):
        """instructor hands the message back to the model, so it has to be
        actionable rather than merely correct."""
        with self.assertRaises(ValidationError) as caught:
            self.validate(2000, 800)
        message = str(caught.exception)
        self.assertIn("800", message)
        self.assertIn("leftovers", message.lower())

    def test_no_budget_in_context_skips_the_check(self):
        """A bare `Recipe.model_validate` of a saved favorite must stay
        loadable rather than blow up."""
        self.assertIsNotNone(
            DayRecipes.model_validate({"recipes": [recipe(2000).model_dump()]})
        )


class TestRejectUntrimmableWeek(unittest.TestCase):
    """Layer 3 on the meal-type axis (`MealTypeWeekRecipes`)."""

    def validate(self, per_day: dict, budgets: dict):
        return MealTypeWeekRecipes.model_validate(
            {"recipes": {day: recipe(cal).model_dump() for day, cal in per_day.items()}},
            context=ctx(day_budgets={day: budget(cal) for day, cal in budgets.items()}),
        )

    def test_days_within_reach_are_accepted(self):
        self.assertIsNotNone(
            self.validate({"Monday": 900, "Tuesday": 700}, {"Monday": 800, "Tuesday": 800})
        )

    def test_one_bad_day_rejects_the_response_and_is_named(self):
        with self.assertRaises(ValidationError) as caught:
            self.validate(
                {"Monday": 800, "Tuesday": 2400}, {"Monday": 800, "Tuesday": 800}
            )
        # The validator's own message, not `str(exception)` — Pydantic appends
        # an echo of the whole input payload, in which every day name appears
        # regardless of which one was at fault.
        message = caught.exception.errors()[0]["msg"]
        self.assertIn("Tuesday: 2400", message)
        self.assertNotIn("Monday", message)

    def test_opposite_misses_do_not_cancel_out(self):
        """Checked per day rather than pooled — a week with one day at +80%
        and another at -80% nets to zero on a pooled total and would let two
        rejectable days hide behind the average."""
        with self.assertRaises(ValidationError) as caught:
            self.validate(
                {"Monday": 2400, "Tuesday": 200}, {"Monday": 800, "Tuesday": 800}
            )
        message = str(caught.exception)
        self.assertIn("Monday", message)
        self.assertIn("Tuesday", message)


class TestTheLayersAgree(unittest.TestCase):
    """The coupling itself — the reason CLAUDE.md says not to replace layer
    3's threshold with a standalone tolerance.

    Rather than restating either number, this sweeps a range of overshoots and
    asserts the two layers partition it identically: every response layer 2
    can place exactly on budget is one layer 3 accepts, and vice versa. Swap
    layer 3 for a flat tolerance and this fails at whichever end is tighter.
    """

    def test_acceptance_matches_the_trims_reach_exactly(self):
        target = 800.0
        # Factors spanning well outside the clamp at both ends, avoiding the
        # exact boundaries where float comparison is the thing under test
        # rather than the behaviour.
        for factor in [0.3, 0.45, 0.55, 0.65, 0.8, 1.0, 1.25, 1.5, 1.55, 1.8, 2.5]:
            with self.subTest(factor=factor):
                # A recipe needing `factor` to reach target has target/factor kcal.
                total = target / factor
                trim_can_fix = LOW <= factor <= HIGH

                _, applied = fit_recipe_to_budget(recipe(total), budget(target))
                reached_budget = abs(applied - factor) < 1e-6

                try:
                    DayRecipes.model_validate(
                        {"recipes": [recipe(total).model_dump()]},
                        context=ctx(day_budget=budget(target)),
                    )
                    accepted = True
                except ValidationError:
                    accepted = False

                self.assertEqual(
                    accepted,
                    trim_can_fix,
                    f"layer 3 {'accepted' if accepted else 'rejected'} a response "
                    f"needing factor {factor}, which the trim "
                    f"{'can' if trim_can_fix else 'cannot'} fix",
                )
                self.assertEqual(
                    reached_budget,
                    trim_can_fix,
                    f"layer 2 {'reached' if reached_budget else 'missed'} budget at "
                    f"factor {factor}",
                )

    def test_the_threshold_follows_a_widened_clamp(self):
        """Derived, not hardcoded: widening the clamp in config must widen
        what layer 3 accepts, with no second number to remember."""
        wide = dict(
            PERMISSIVE_CONFIG,
            planning_rules=dict(DEFAULT_PLANNING_RULES, portion_trim_limits=(0.3, 3.0)),
        )
        self.assertEqual(planning_rule(wide, "portion_trim_limits"), (0.3, 3.0))

        # Needs a 0.4 factor: rejected under the default clamp, accepted here.
        payload = {"recipes": [recipe(2000).model_dump()]}
        with self.assertRaises(ValidationError):
            DayRecipes.model_validate(payload, context=ctx(day_budget=budget(800)))
        self.assertIsNotNone(
            DayRecipes.model_validate(
                payload, context=ctx(day_budget=budget(800), config=wide)
            )
        )


if __name__ == "__main__":
    unittest.main()
