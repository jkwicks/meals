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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402

# The cook days a whole-week generation call covers — `build_diet_style_rule`
# is per call now, and a rule that binds on only some of them has to say so.
WEEK = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]

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
        self.assertEqual(planner.build_diet_style_rule(config, WEEK), "")

    def test_missing_diet_styles_key_emits_nothing(self):
        # A config predating this feature has no `diet_styles` key at all —
        # must behave exactly like an empty catalog, not KeyError.
        config = {"dietary_rules": dict(BASE_CONFIG["dietary_rules"])}
        self.assertEqual(planner.build_diet_style_rule(config, WEEK), "")

    def test_active_style_names_label_and_principles(self):
        config = dict(
            BASE_CONFIG,
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=["fast_800"]),
        )
        rule = planner.build_diet_style_rule(config, WEEK)
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
        rule = planner.build_diet_style_rule(config, WEEK)
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
        self.assertIsNone(planner.diet_style_calorie_ceiling(self.config([], fast_800=800), "Monday"))

    def test_an_active_style_without_one_has_no_ceiling(self):
        """Eleven of the twelve shipped styles declare nothing, and must go on
        meaning "this style says nothing about the day's energy"."""
        self.assertIsNone(
            planner.diet_style_calorie_ceiling(self.config(["mediterranean_diet"]), "Monday")
        )

    def test_an_active_style_declaring_one_reports_it(self):
        self.assertEqual(
            planner.diet_style_calorie_ceiling(self.config(["fast_800"], fast_800=800), "Monday"),
            800.0,
        )

    def test_the_lowest_wins_when_two_declare_one(self):
        """Two bounds are two bounds and only the tighter is actually kept.
        Averaging would produce a number neither style asked for — the same
        reason `reconcile_adaptive_tdee` picks one TDEE rather than blending."""
        config = self.config(
            ["mediterranean_diet", "fast_800"], mediterranean_diet=1600, fast_800=800
        )
        self.assertEqual(planner.diet_style_calorie_ceiling(config, "Monday"), 800.0)

    def test_a_config_predating_the_field_reports_nothing(self):
        """No `diet_styles` key at all — the same tolerance
        `build_diet_style_rule` extends, not a KeyError."""
        self.assertIsNone(
            planner.diet_style_calorie_ceiling(
                {"dietary_rules": {"active_diet_styles": ["fast_800"]}}, "Monday"
            )
        )

    def test_the_rule_text_never_states_the_number(self):
        """The ceiling is applied where the day's calories are decided and is
        deliberately not restated in the prompt: a model told the number
        starts optimising for it instead of for the food, which is the failure
        `FIBER_REPORTING_RULE`'s second sentence exists to head off."""
        rule = planner.build_diet_style_rule(self.config(["fast_800"], fast_800=800), WEEK)
        self.assertIn("Fast 800", rule)
        self.assertNotIn("800 kcal", rule)


class TestDayScopedEntriesParser(unittest.TestCase):
    """`day_scoped_entries` — the one parser of a list that mixes bare names
    with `{style, days}` windows, and the substrate a block's mid-week
    boundary reuses (`design-01` §5).

    All six cases below are load-time and all six are *stated* rather than
    discovered, which is why each has a test: three features will depend on
    this schema, and a schema three features depend on earns assertions on
    its edges. Four of them raise, where `inventory_entries` drops a
    malformed entry with a warning — the two fields differ in what a silent
    drop costs, and the docstring on the parser records why.
    """

    def parse(self, entries, **kwargs):
        return planner.day_scoped_entries(entries, "test_field", **kwargs)

    def test_a_bare_name_means_every_day(self):
        """The shape every config predating this holds, and the one the
        preset editor's multi-select writes."""
        self.assertEqual(self.parse(["fast_800"]), {"fast_800": None})

    def test_a_window_names_its_days(self):
        self.assertEqual(
            self.parse([{"style": "fast_800", "days": ["Monday", "Tuesday"]}]),
            {"fast_800": ["Monday", "Tuesday"]},
        )

    def test_an_unknown_weekday_raises_naming_the_style_and_the_day(self):
        with self.assertRaises(ValueError) as caught:
            self.parse([{"style": "fast_800", "days": ["Monday", "Moonday"]}])
        self.assertIn("fast_800", str(caught.exception))
        self.assertIn("Moonday", str(caught.exception))

    def test_an_empty_days_list_raises(self):
        """"Active on no days" is indistinguishable from a mistake, and the
        way to express it is to remove the entry."""
        with self.assertRaises(ValueError) as caught:
            self.parse([{"style": "fast_800", "days": []}])
        self.assertIn("fast_800", str(caught.exception))

    def test_a_window_with_no_days_key_raises(self):
        """The bare string is how you say every day; an object that omits
        `days` is a half-written window, not a synonym for one."""
        with self.assertRaises(ValueError) as caught:
            self.parse([{"style": "fast_800"}])
        self.assertIn("days", str(caught.exception))

    def test_the_same_style_twice_unions_its_days(self):
        """Two windows onto one style are two windows. Not an error."""
        self.assertEqual(
            self.parse([
                {"style": "fast_800", "days": ["Monday", "Tuesday"]},
                {"style": "fast_800", "days": ["Tuesday", "Friday"]},
            ]),
            {"fast_800": ["Monday", "Tuesday", "Friday"]},
        )

    def test_bare_beside_scoped_resolves_to_every_day_and_warns(self):
        """The wider claim wins — the narrow one adds nothing — so this is
        redundant rather than wrong, and warns rather than raising."""
        for entries in (
            ["fast_800", {"style": "fast_800", "days": ["Monday"]}],
            [{"style": "fast_800", "days": ["Monday"]}, "fast_800"],
        ):
            with self.subTest(entries=entries):
                with self.assertLogs(planner.logger, "WARNING") as logs:
                    parsed = self.parse(entries, warn=True)
                self.assertEqual(parsed, {"fast_800": None})
                self.assertIn("fast_800", "".join(logs.output))

    def test_the_redundancy_warning_is_silent_unless_asked_for(self):
        """`AppConfig` asks once per load; the per-day readers never do.
        Hydration runs on every UI repaint, and a warning per day per repaint
        would bury the per-call generation timing `logs/meals.log` exists for.
        """
        with mock.patch.object(planner.logger, "warning") as warned:
            self.parse(["fast_800", {"style": "fast_800", "days": ["Monday"]}])
        warned.assert_not_called()

    def test_a_day_outside_the_planning_week_is_inert(self):
        """The week rotates by `week_start_day` and a window is a statement
        about days, not about which days a given week reaches — so naming a
        Sunday a four-day grid never touches is legal and simply never
        matches."""
        parsed = self.parse([{"style": "fast_800", "days": ["Saturday", "Sunday"]}])
        self.assertEqual(parsed, {"fast_800": ["Saturday", "Sunday"]})
        self.assertEqual(
            planner.build_diet_style_rule(
                dict(
                    BASE_CONFIG,
                    dietary_rules=dict(
                        BASE_CONFIG["dietary_rules"],
                        active_diet_styles=[
                            {"style": "fast_800", "days": ["Saturday", "Sunday"]}
                        ],
                    ),
                ),
                ["Monday", "Tuesday"],
            ),
            "",
        )

    def test_a_shape_that_is_neither_raises(self):
        with self.assertRaises(ValueError):
            self.parse([17])

    def test_an_unknown_key_beside_the_two_raises(self):
        """Same `extra="forbid"` policy every other config object gets: a
        typo'd key names itself at load rather than being ignored."""
        with self.assertRaises(ValueError) as caught:
            self.parse([{"style": "fast_800", "day": ["Monday"]}])
        self.assertIn("day", str(caught.exception))

    def test_the_subject_key_is_the_callers(self):
        """Kept general because a block asks the same question of a different
        subject — `design-01` §5, which is why the day-scoping is not buried
        inside `diet_style_calorie_ceiling`."""
        self.assertEqual(
            planner.day_scoped_entries(
                [{"block": "cut", "days": ["Monday"]}], "blocks", subject_key="block"
            ),
            {"cut": ["Monday"]},
        )


class TestDayScopedDietStyleRule(unittest.TestCase):
    """`build_diet_style_rule` is per call, and the two generation axes
    differ: `generate_meal_type_week` spans the week, `generate_day` covers
    one day. A week-spanning call under a four-day window has to say which
    nights each principle binds on — the same problem `_sourcing_day_split`
    solves for a Saturday-only fishmonger, and the same function solving it.
    """

    # The exact text a flat list produced before day-scoping existed. Pinned
    # as a literal rather than compared against another call of the same
    # function, because "byte-identical to before" is a claim about the past
    # and only a literal can hold the old value.
    FLAT = (
        "- This week also follows these standing dietary approaches, in "
        "addition to (not instead of) each meal's cuisine — cuisine is the "
        "flavour tradition, a dietary approach is what to prioritize within "
        "it, and a dish should satisfy both at once:\n"
        "  - Fast 800: Keep dishes simple and calorie-light.\n"
    )

    def config(self, active) -> dict:
        return dict(
            BASE_CONFIG,
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=active),
        )

    def test_a_flat_list_is_byte_identical_to_before(self):
        """The compatibility claim that matters most. A bare name means every
        day whatever this call covers, so it is stated unconditionally and
        the text is the one it always was."""
        self.assertEqual(planner.build_diet_style_rule(self.config(["fast_800"]), WEEK), self.FLAT)
        self.assertEqual(
            planner.build_diet_style_rule(self.config(["fast_800"]), ["Monday"]), self.FLAT
        )

    def test_an_empty_list_is_byte_identical_to_before_the_feature(self):
        """Not just empty text — the whole rules block must match a config
        that has never heard of the key."""
        without = dict(BASE_CONFIG, dietary_rules={
            "allowed_nova_groups": [1, 2, 3], "banned_ingredients": [],
        })
        rules = lambda config: planner.build_generation_rules(  # noqa: E731
            config, days=WEEK, style_rule=planner.WEEK_STYLE_RULE,
            variety_rule=planner.WEEK_VARIETY_RULE, budget_rule=planner.WEEK_BUDGET_RULE,
        )
        self.assertEqual(rules(self.config([])), rules(without))

    def test_a_straddling_week_call_names_the_days(self):
        rule = planner.build_diet_style_rule(
            self.config([{"style": "fast_800", "days": ["Monday", "Tuesday"]}]), WEEK
        )
        self.assertIn("Fast 800 (on Monday, Tuesday only):", rule)
        self.assertIn("Keep dishes simple and calorie-light.", rule)

    def test_a_call_wholly_inside_the_window_is_unconditional(self):
        """`generate_day` on a Monday under a Monday-Thursday window: there
        is nothing to qualify, so it gets the plain wording rather than a
        parenthetical restating the day it was already asked about."""
        rule = planner.build_diet_style_rule(
            self.config([{"style": "fast_800", "days": ["Monday", "Tuesday"]}]), ["Monday"]
        )
        self.assertEqual(rule, self.FLAT)

    def test_a_call_wholly_outside_the_window_drops_the_style(self):
        """A Friday `generate_day` under a Monday-Thursday window. Briefing
        the model against a rule that does not bind on the night it is
        cooking is worse than silence."""
        rule = planner.build_diet_style_rule(
            self.config([{"style": "fast_800", "days": ["Monday", "Tuesday"]}]), ["Friday"]
        )
        self.assertEqual(rule, "")

    def test_a_bare_style_beside_a_scoped_one_keeps_its_unconditional_line(self):
        rule = planner.build_diet_style_rule(
            self.config([
                "mediterranean_diet",
                {"style": "fast_800", "days": ["Monday", "Tuesday"]},
            ]),
            WEEK,
        )
        self.assertIn("  - Mediterranean Diet: Favor olive oil and oily fish.\n", rule)
        self.assertIn("Fast 800 (on Monday, Tuesday only):", rule)

    def test_the_scoped_rule_still_never_states_the_number(self):
        config = dict(
            BASE_CONFIG,
            diet_styles=dict(
                BASE_CONFIG["diet_styles"],
                fast_800=dict(BASE_CONFIG["diet_styles"]["fast_800"], calorie_ceiling=800),
            ),
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"],
                active_diet_styles=[{"style": "fast_800", "days": ["Monday"]}],
            ),
        )
        rule = planner.build_diet_style_rule(config, WEEK)
        self.assertIn("Fast 800 (on Monday only):", rule)
        self.assertNotIn("800 kcal", rule)


class TestDayScopedCalorieCeiling(unittest.TestCase):
    """The reading half of "Fast 800 for four days". What hydration does with
    the number is pinned in `test_planner_dynamic_targets.py`."""

    def config(self, active) -> dict:
        return dict(
            BASE_CONFIG,
            diet_styles=dict(
                BASE_CONFIG["diet_styles"],
                fast_800=dict(BASE_CONFIG["diet_styles"]["fast_800"], calorie_ceiling=800),
                mediterranean_diet=dict(
                    BASE_CONFIG["diet_styles"]["mediterranean_diet"], calorie_ceiling=1600
                ),
            ),
            dietary_rules=dict(BASE_CONFIG["dietary_rules"], active_diet_styles=active),
        )

    def test_a_window_binds_only_inside_itself(self):
        config = self.config([{"style": "fast_800", "days": ["Monday", "Tuesday"]}])
        self.assertEqual(planner.diet_style_calorie_ceiling(config, "Monday"), 800.0)
        self.assertIsNone(planner.diet_style_calorie_ceiling(config, "Friday"))

    def test_a_bare_style_still_binds_every_day(self):
        config = self.config(["fast_800"])
        for day in WEEK:
            self.assertEqual(planner.diet_style_calorie_ceiling(config, day), 800.0)

    def test_the_lowest_of_the_styles_active_that_day_wins(self):
        """Lowest-wins is per day now: Monday sees both bounds and keeps the
        tighter, Friday sees only the one whose window reaches it."""
        config = self.config([
            "mediterranean_diet",
            {"style": "fast_800", "days": ["Monday"]},
        ])
        self.assertEqual(planner.diet_style_calorie_ceiling(config, "Monday"), 800.0)
        self.assertEqual(planner.diet_style_calorie_ceiling(config, "Friday"), 1600.0)


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


    def test_a_style_named_inside_a_window_is_cross_checked_too(self):
        """The catalog check reads the list through the same parser, so both
        shapes are checked. A window is not a way to smuggle a typo past it."""
        raw = dict(
            BASE_CONFIG,
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"],
                active_diet_styles=[{"style": "not_a_real_diet", "days": ["Monday"]}],
            ),
        )
        with self.assertRaises(ValueError) as caught:
            planner.load_app_config(raw)
        self.assertIn("not_a_real_diet", str(caught.exception))

    def test_a_window_loads_cleanly_and_survives_validation(self):
        raw = dict(
            BASE_CONFIG,
            dietary_rules=dict(
                BASE_CONFIG["dietary_rules"],
                active_diet_styles=[
                    "mediterranean_diet",
                    {"style": "fast_800", "days": ["Monday", "Tuesday"]},
                ],
            ),
        )
        config = planner.load_app_config(raw)
        # Stored as written — the parser is the only reader, so `AppConfig`
        # has no business normalising the two shapes into one on the way past.
        self.assertEqual(
            config["dietary_rules"]["active_diet_styles"],
            ["mediterranean_diet", {"style": "fast_800", "days": ["Monday", "Tuesday"]}],
        )

    def test_a_malformed_window_fails_at_load(self):
        """The parser's own failures reach the same place a typo'd style name
        does — one field, one policy, and the loud one."""
        for entry in (
            {"style": "fast_800"},
            {"style": "fast_800", "days": []},
            {"style": "fast_800", "days": ["Moonday"]},
        ):
            with self.subTest(entry=entry):
                raw = dict(
                    BASE_CONFIG,
                    dietary_rules=dict(
                        BASE_CONFIG["dietary_rules"], active_diet_styles=[entry]
                    ),
                )
                with self.assertRaises(ValueError):
                    planner.load_app_config(raw)


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
        self.assertIsNone(planner.diet_style_calorie_ceiling(config, "Monday"))


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
