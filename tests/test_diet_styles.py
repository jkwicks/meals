"""Tests for the diet-style axis: a standing eating pattern (Mediterranean,
Fast 800, DASH, Total Wellbeing Diet) layered on top of cuisine.

Two things need covering: `AppConfig` rejects an `active_diet_styles` entry
the `diet_styles` catalog doesn't know about (the same "fail at load, name
the typo" policy every other section here gets), and
`build_diet_style_rule` emits nothing when no style is active and the
right guidance text when one is. `unittest` and the `sys.path` insert match
`test_week_composition.py`; see its docstring for why.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402

BASE_CONFIG = {
    "weekly_schedule": {
        "Monday": {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55},
    },
    "dietary_rules": {
        "allowed_nova_groups": [1, 2, 3],
        "banned_ingredients": [],
    },
    "diet_styles": {
        "mediterranean_diet": {
            "label": "Mediterranean Diet",
            "principles": "Favor olive oil and oily fish.",
        },
        "fast_800": {
            "label": "Fast 800",
            "principles": "Keep dishes simple and calorie-light.",
        },
    },
}


class TestBuildDietStyleRule(unittest.TestCase):
    def test_no_active_styles_emits_nothing(self):
        config = dict(BASE_CONFIG, dietary_rules=dict(BASE_CONFIG["dietary_rules"]))
        self.assertEqual(planner.build_diet_style_rule(config), "")

    def test_missing_diet_styles_key_emits_nothing(self):
        # A config predating this feature has no `diet_styles` key at all —
        # must behave exactly like an empty catalog, not KeyError.
        config = {"dietary_rules": dict(BASE_CONFIG["dietary_rules"])}
        self.assertEqual(planner.build_diet_style_rule(config), "")

    def test_active_style_names_label_and_principles(self):
        config = dict(
            BASE_CONFIG,
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=["fast_800"]),
        )
        rule = planner.build_diet_style_rule(config)
        self.assertIn("Fast 800", rule)
        self.assertIn("Keep dishes simple and calorie-light.", rule)
        self.assertNotIn("Mediterranean", rule)

    def test_multiple_active_styles_all_appear(self):
        config = dict(
            BASE_CONFIG,
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"],
                active_diet_styles=["mediterranean_diet", "fast_800"],
            ),
        )
        rule = planner.build_diet_style_rule(config)
        self.assertIn("Mediterranean Diet", rule)
        self.assertIn("Fast 800", rule)

    def test_rule_folds_into_build_generation_rules(self):
        config = dict(
            BASE_CONFIG,
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=["fast_800"]),
        )
        rules = planner.build_generation_rules(
            config,
            days=["Monday"],
            style_rule=planner.DAY_STYLE_RULE,
            variety_rule=planner.DAY_VARIETY_RULE,
            budget_rule=planner.DAY_BUDGET_RULE,
        )
        self.assertIn("Fast 800", rules)


class TestDietStyleCalorieCeiling(unittest.TestCase):
    """`diet_style_calorie_ceiling` — the one numeric lever a diet style has.

    The reading half only. What hydration then *does* with the number is
    pinned in `test_planner_dynamic_targets.py`, which owns the fixture and
    the hand-worked figures the cap is measured against — the same split the
    code makes, since this function knows nothing about a day.
    """

    def catalog(self, **ceilings) -> dict:
        styles = {
            key: dict(entry) for key, entry in BASE_CONFIG["diet_styles"].items()
        }
        for key, value in ceilings.items():
            styles[key]["calorie_ceiling"] = value
        return styles

    def config(self, active, **ceilings) -> dict:
        return dict(
            BASE_CONFIG,
            diet_styles=self.catalog(**ceilings),
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=active),
        )

    def test_no_active_style_has_no_ceiling(self):
        self.assertIsNone(planner.diet_style_calorie_ceiling(self.config([], fast_800=800)))

    def test_an_active_style_without_one_has_no_ceiling(self):
        """Eleven of the twelve shipped styles declare nothing, and must go on
        meaning "this style says nothing about the day's energy"."""
        self.assertIsNone(
            planner.diet_style_calorie_ceiling(self.config(["mediterranean_diet"]))
        )

    def test_an_active_style_declaring_one_reports_it(self):
        self.assertEqual(
            planner.diet_style_calorie_ceiling(self.config(["fast_800"], fast_800=800)),
            800.0,
        )

    def test_the_lowest_wins_when_two_declare_one(self):
        """Two bounds are two bounds and only the tighter is actually kept.
        Averaging would produce a number neither style asked for — the same
        reason `reconcile_adaptive_tdee` picks one TDEE rather than blending."""
        config = self.config(
            ["mediterranean_diet", "fast_800"], mediterranean_diet=1600, fast_800=800
        )
        self.assertEqual(planner.diet_style_calorie_ceiling(config), 800.0)

    def test_a_config_predating_the_field_reports_nothing(self):
        """No `diet_styles` key at all — the same tolerance
        `build_diet_style_rule` extends, not a KeyError."""
        self.assertIsNone(
            planner.diet_style_calorie_ceiling(
                {"dietary_rules": {"active_diet_styles": ["fast_800"]}}
            )
        )

    def test_the_rule_text_never_states_the_number(self):
        """The ceiling is applied where the day's calories are decided and is
        deliberately not restated in the prompt: a model told the number
        starts optimising for it instead of for the food, which is the failure
        `FIBER_REPORTING_RULE`'s second sentence exists to head off."""
        rule = planner.build_diet_style_rule(self.config(["fast_800"], fast_800=800))
        self.assertIn("Fast 800", rule)
        self.assertNotIn("800 kcal", rule)


class TestAppConfigValidatesDietStyles(unittest.TestCase):
    def test_unknown_active_style_fails_at_load(self):
        raw = dict(
            BASE_CONFIG,
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"], active_diet_styles=["not_a_real_diet"]
            ),
        )
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_known_active_style_loads_cleanly(self):
        raw = dict(
            BASE_CONFIG,
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"], active_diet_styles=["mediterranean_diet"]
            ),
        )
        config = planner.load_app_config(raw)
        self.assertEqual(config["dietary_rules"]["active_diet_styles"], ["mediterranean_diet"])
        self.assertIn("mediterranean_diet", config["diet_styles"])


class TestRealConfig(unittest.TestCase):
    """The shipped config/ still validates with the diet_styles catalog in it."""

    def test_shipped_diet_styles_catalog_loads(self):
        config = planner.load_app_config(run_sync(LocalJSONRepository().load_config()))
        self.assertIn("mediterranean_diet", config["diet_styles"])
        self.assertIn("fast_800", config["diet_styles"])
        self.assertEqual(config["dietary_rules"]["active_diet_styles"], [])
        # Shipped, but inert: nothing is active, so `diet_style_calorie_ceiling`
        # reports None and every day plans exactly as it did before the field
        # existed.
        self.assertEqual(config["diet_styles"]["fast_800"]["calorie_ceiling"], 800.0)
        self.assertIsNone(config["diet_styles"]["mediterranean_diet"]["calorie_ceiling"])
        self.assertIsNone(planner.diet_style_calorie_ceiling(config))


class TestIngredientRulesReadTheirContext(unittest.TestCase):
    """`Ingredient`'s two hard rules, and the four shapes of validation
    context they have to survive.

    Unlike `diet_styles` (soft guidance in the prompt), `allowed_nova_groups`
    and `banned_ingredients` are enforced as schema validation — instructor
    catches the raise and hands the model its own output back. Both read live
    config out of `info.context`, and both must still load a saved favorite
    validated with no config at all.

    The `{"config": None}` row is the one worth having. The original guard
    tested `"config" in info.context` and then subscripted it, so that shape
    passed the check and raised `TypeError` from inside Pydantic — while
    `reject_untrimmable_macro_miss` read the same context tolerantly. Two
    conventions for one thing, one of which crashed.
    """

    RAW = {
        "name": "Chicken breast", "quantity_g": 100, "nova_group": 1,
        "calories": 165, "protein_g": 31, "net_carbs_g": 0, "fat_g": 3.6,
    }
    CONTEXTS = {
        "no context at all": None,
        "an empty context": {},
        "an explicit None config": {"config": None},
        "a config with no dietary_rules": {"config": {}},
    }

    def test_a_clean_ingredient_loads_under_every_context_shape(self):
        for label, context in self.CONTEXTS.items():
            with self.subTest(context=label):
                self.assertIsNotNone(
                    planner.Ingredient.model_validate(self.RAW, context=context)
                )

    def test_the_nova_default_still_applies_without_config(self):
        """Tolerant about *finding* the rule, never about enforcing it —
        group 4 is rejected whether or not a config was supplied."""
        for label, context in self.CONTEXTS.items():
            with self.subTest(context=label):
                with self.assertRaises(Exception):
                    planner.Ingredient.model_validate(
                        dict(self.RAW, nova_group=4), context=context
                    )

    def test_live_config_overrides_the_default(self):
        context = {"config": {"dietary_rules": {
            "allowed_nova_groups": [1], "banned_ingredients": []}}}
        self.assertIsNotNone(
            planner.Ingredient.model_validate(self.RAW, context=context)
        )
        with self.assertRaises(Exception):
            planner.Ingredient.model_validate(
                dict(self.RAW, nova_group=2), context=context
            )

    def test_a_banned_substring_is_matched_inside_a_longer_name(self):
        """The list holds "seed oils"; a model writes "refined seed oils
        blend" far more often than it writes the bare term."""
        context = {"config": {"dietary_rules": {
            "allowed_nova_groups": [1, 2, 3], "banned_ingredients": ["seed oils"]}}}
        with self.assertRaises(Exception) as caught:
            planner.Ingredient.model_validate(
                dict(self.RAW, name="Refined seed oils blend"), context=context
            )
        self.assertIn("seed oils", str(caught.exception))

    def test_nothing_is_banned_when_no_config_says_so(self):
        for label, context in self.CONTEXTS.items():
            with self.subTest(context=label):
                self.assertIsNotNone(
                    planner.Ingredient.model_validate(
                        dict(self.RAW, name="Refined seed oils blend"), context=context
                    )
                )


if __name__ == "__main__":
    unittest.main()
