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


if __name__ == "__main__":
    unittest.main()
