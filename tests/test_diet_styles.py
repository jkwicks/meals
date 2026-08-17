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
            style_rule=planner.DAY_STYLE_RULE,
            variety_rule=planner.DAY_VARIETY_RULE,
            budget_rule=planner.DAY_BUDGET_RULE,
        )
        self.assertIn("Fast 800", rules)


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
