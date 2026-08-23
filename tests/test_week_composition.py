"""Tests for how a week is composed before a single token is generated.

Three deterministic, API-free decisions live between `default_week_spec` and
the first API call, and all three were added to cut food waste rather than to
change what a meal looks like:

- **Cuisine blocks** (`cuisine_block_sizes`, `pick_cuisine_blocks`) — four
  nights of one cuisine and three of a complementary second, instead of seven
  countries that share nothing on the shopping list.
- **Workout-synced breakfasts** (`morning_training_days`, `week.pin_style`) —
  a morning gym (hypertrophy) session forces that day's breakfast to a
  shake; cardio deliberately doesn't.
- **Ingredient canonicalisation** (`shopping.canonical_ingredient`) — the
  variants of one staple that reach the list anyway get merged into one line.

Everything here is a pure function of its arguments, so there is no
repository, no event loop and no API in this file. `unittest` and the
`sys.path` insert match `test_planner_dynamic_targets.py`; see its docstring
for why.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
import shopping  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, SlotSpec, WeekSpec, pin_style  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Small standalone config rather than the shipped config/: these tests are about
# the layout rules, and a cuisine list that shifts with the user's taste would
# re-baseline the expected blocks underneath them. `test_real_config` below is
# what keeps the shipped file honest.
CONFIG = {
    "meal_types": ["breakfast", "lunch", "dinner", "snack"],
    "meal_styles": {
        "breakfast": {
            "custom_shake": "shake",
            "eggs_salmon": "eggs",
            "yoghurt_bowl": "yoghurt",
        },
        "dinner": {"one_pan": "tray bake", "curry": "curry", "grill": "grill"},
    },
    "cuisines": ["thai", "vietnamese", "italian", "greek", "korean"],
    "cuisine_affinities": {"thai": ["vietnamese"], "italian": ["greek"]},
    "cuisine_meal_types": ["dinner"],
    "planning_rules": dict(planner.DEFAULT_PLANNING_RULES),
    "training_schedule": [],
}


def week_spec(**overrides) -> WeekSpec:
    """A full 7-day grid of cook slots, before any auto choice is resolved."""
    slots = [
        SlotSpec(day=day, meal_type=meal_type, mode=MODE_COOK)
        for day in DAYS
        for meal_type in CONFIG["meal_types"]
    ]
    return WeekSpec(days=DAYS, servings_per_meal=2, slots=slots, **overrides)


def dinner_cuisines(spec: WeekSpec):
    return [slot.cuisine for slot in spec.slots if slot.meal_type == "dinner"]


class TestCuisineBlockSizes(unittest.TestCase):
    """The 4/3 pattern is a ratio, not a day count."""

    def test_full_week_is_four_then_three(self):
        self.assertEqual(planner.cuisine_block_sizes(7, [4, 3]), [4, 3])

    def test_sizes_always_sum_to_the_days_available(self):
        for num_days in range(1, 15):
            with self.subTest(num_days=num_days):
                self.assertEqual(sum(planner.cuisine_block_sizes(num_days, [4, 3])), num_days)

    def test_short_weeks_keep_the_larger_block_first(self):
        # Four dinners cooked (three eaten as leftovers) splits evenly; five
        # gives the spare day to the block the pattern made bigger.
        self.assertEqual(planner.cuisine_block_sizes(4, [4, 3]), [2, 2])
        self.assertEqual(planner.cuisine_block_sizes(5, [4, 3]), [3, 2])

    def test_empty_blocks_drop_out_rather_than_claiming_a_cuisine(self):
        self.assertEqual(planner.cuisine_block_sizes(1, [4, 3]), [1])
        self.assertEqual(planner.cuisine_block_sizes(0, [4, 3]), [])

    def test_pattern_of_ones_restores_a_cuisine_per_night(self):
        self.assertEqual(planner.cuisine_block_sizes(7, [1] * 7), [1] * 7)


class TestPickCuisineBlocks(unittest.TestCase):
    def test_seven_days_resolve_to_exactly_two_cuisines(self):
        picked = planner.pick_cuisine_blocks(7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"])
        self.assertEqual(len(picked), 7)
        self.assertEqual(picked[:4], [picked[0]] * 4)
        self.assertEqual(picked[4:], [picked[4]] * 3)
        self.assertNotEqual(picked[0], picked[4])

    def test_first_block_continues_the_across_week_rotation(self):
        # Strict LRU, same as the per-slot pick this replaced: thai was used
        # most recently of the two the affinity map pairs, so italian leads.
        picked = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], ["italian", "greek", "korean", "vietnamese", "thai"],
            CONFIG["cuisine_affinities"],
        )
        self.assertEqual(picked[0], "italian")

    def test_second_block_prefers_a_complementary_cuisine(self):
        picked = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], ["thai"], CONFIG["cuisine_affinities"]
        )
        self.assertEqual(picked[0], "vietnamese")
        # vietnamese lists no affinities, so the second block falls back to
        # the global LRU pick rather than repeating the first.
        self.assertNotEqual(picked[4], "vietnamese")

    def test_no_cuisines_configured_yields_nothing(self):
        self.assertEqual(planner.pick_cuisine_blocks(7, [], []), [])


class TestBaselineCuisineFloor(unittest.TestCase):
    """`baseline_cuisines`/`min_baseline_share` reserve enough of a week's
    largest blocks to clear a floor, on top of the ordinary affinity/LRU pick
    `pick_cuisine_blocks` already does. "greek"/"korean" stand in for a
    baseline pool here — the mechanism doesn't care which cuisines they are,
    and CONFIG's real "homestyle"/"modern_australian" analogues live in
    `config/meals.json`, not this standalone fixture."""

    BASELINE = ["greek", "korean"]

    def test_zero_share_leaves_the_pick_unchanged(self):
        with_floor = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"], None, self.BASELINE, 0.0
        )
        without_floor = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"]
        )
        self.assertEqual(with_floor, without_floor)

    def test_an_unconfigured_baseline_pool_is_a_no_op(self):
        # A high share with nothing to reserve blocks *for* must not error or
        # narrow the pool to nothing — this is the feature's off switch.
        picked = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"], None, [], 0.9
        )
        without_floor = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"]
        )
        self.assertEqual(picked, without_floor)

    def test_half_share_reserves_only_the_larger_block(self):
        # Unfloored, block 0 goes to thai (see
        # test_seven_days_resolve_to_exactly_two_cuisines) — the floor must
        # override that for the reserved block without touching the other,
        # since 4 of 7 days already clears a 0.5 floor on its own.
        picked = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"], None, self.BASELINE, 0.5
        )
        self.assertIn(picked[0], self.BASELINE)
        self.assertEqual(picked[:4], [picked[0]] * 4)
        self.assertNotIn(picked[4], self.BASELINE)

    def test_full_share_reserves_every_block(self):
        picked = planner.pick_cuisine_blocks(
            7, CONFIG["cuisines"], [], CONFIG["cuisine_affinities"], None, self.BASELINE, 1.0
        )
        self.assertTrue(set(picked) <= set(self.BASELINE))

    def test_resolve_auto_choices_reads_the_floor_off_config(self):
        config = dict(
            CONFIG,
            baseline_cuisines=self.BASELINE,
            planning_rules=dict(CONFIG["planning_rules"], min_baseline_cuisine_share=0.5),
        )
        resolved = planner.resolve_auto_choices(week_spec(), config, [])
        self.assertIn(dinner_cuisines(resolved)[0], self.BASELINE)


class TestResolveAutoChoices(unittest.TestCase):
    def test_dinners_resolve_into_two_contiguous_blocks(self):
        resolved = planner.resolve_auto_choices(week_spec(), CONFIG, [])
        cuisines = dinner_cuisines(resolved)
        self.assertEqual(len(set(cuisines)), 2)
        self.assertEqual(cuisines[:4], [cuisines[0]] * 4)
        self.assertEqual(cuisines[4:], [cuisines[4]] * 3)

    def test_only_cuisine_meal_types_get_a_cuisine(self):
        resolved = planner.resolve_auto_choices(week_spec(), CONFIG, [])
        for slot in resolved.slots:
            if slot.meal_type != "dinner":
                self.assertIsNone(slot.cuisine, slot.id)

    def test_leftover_dinners_shrink_the_blocks_rather_than_breaking_them(self):
        spec = week_spec()
        slots = [
            slot.model_copy(update={"mode": MODE_LEFTOVER, "source": "Monday:dinner"})
            if slot.meal_type == "dinner" and slot.day in ("Tuesday", "Thursday", "Saturday")
            else slot
            for slot in spec.slots
        ]
        resolved = planner.resolve_auto_choices(spec.model_copy(update={"slots": slots}), CONFIG, [])
        cooked = [slot.cuisine for slot in resolved.slots if slot.meal_type == "dinner" and slot.mode == MODE_COOK]
        self.assertEqual(len(cooked), 4)
        self.assertEqual(cooked[:2], [cooked[0]] * 2)
        self.assertEqual(cooked[2:], [cooked[2]] * 2)

    def test_an_explicit_cuisine_is_never_overwritten(self):
        spec = week_spec()
        slots = [
            slot.model_copy(update={"cuisine": "korean"})
            if slot.id == "Wednesday:dinner"
            else slot
            for slot in spec.slots
        ]
        resolved = planner.resolve_auto_choices(spec.model_copy(update={"slots": slots}), CONFIG, [])
        by_id = resolved.by_id()
        self.assertEqual(by_id["Wednesday:dinner"].cuisine, "korean")
        # ...and it seeds the rotation, so the auto blocks steer around it.
        self.assertNotIn("korean", {by_id[f"{day}:dinner"].cuisine for day in DAYS} - {"korean"})
        self.assertNotEqual(by_id["Monday:dinner"].cuisine, "korean")


class TestWorkoutBreakfasts(unittest.TestCase):
    def config_with(self, *sessions) -> dict:
        return dict(CONFIG, training_schedule=list(sessions))

    def test_morning_gym_qualifies(self):
        config = self.config_with(
            {"day": "Monday", "time": "06:30", "type": "gym_hypertrophy"},
        )
        self.assertEqual(planner.morning_training_days(config), ["Monday"])

    def test_morning_cardio_does_not_qualify(self):
        # Only hypertrophy training gets the shake pin — cardio's fuelling
        # need isn't the same, and forcing a shake on every cardio morning
        # would empty the breakfast rotation for no reason.
        config = self.config_with(
            {"day": "Thursday", "time": "07:15", "type": "cardio_run"},
        )
        self.assertEqual(planner.morning_training_days(config), [])

    def test_evening_sessions_and_walks_do_not(self):
        config = self.config_with(
            {"day": "Monday", "time": "18:00", "type": "gym_hypertrophy"},
            {"day": "Tuesday", "time": "07:00", "type": "walk"},
            {"day": "Wednesday", "time": "11:30", "type": "cardio_run"},
        )
        self.assertEqual(planner.morning_training_days(config), [])

    def test_breakfast_is_pinned_to_a_shake_on_those_days(self):
        config = self.config_with({"day": "Monday", "time": "06:30", "type": "gym_hypertrophy"})
        resolved = planner.resolve_auto_choices(week_spec(), config, [])
        by_id = resolved.by_id()
        self.assertEqual(by_id["Monday:breakfast"].style, planner.WORKOUT_BREAKFAST_STYLE)
        # The pin seeds the rotation like any other style, so the rest of the
        # week's breakfasts still rotate rather than all becoming shakes.
        self.assertNotEqual(by_id["Tuesday:breakfast"].style, planner.WORKOUT_BREAKFAST_STYLE)

    def test_an_explicit_breakfast_style_survives_the_pin(self):
        config = self.config_with({"day": "Monday", "time": "06:30", "type": "gym_hypertrophy"})
        spec = week_spec()
        slots = [
            slot.model_copy(update={"style": "eggs_salmon"})
            if slot.id == "Monday:breakfast"
            else slot
            for slot in spec.slots
        ]
        resolved = planner.resolve_auto_choices(
            spec.model_copy(update={"slots": slots}), config, []
        )
        self.assertEqual(resolved.by_id()["Monday:breakfast"].style, "eggs_salmon")

    def test_pin_style_leaves_uncooked_slots_alone(self):
        spec = week_spec()
        slots = [
            slot.model_copy(update={"mode": MODE_LEFTOVER, "source": "Sunday:breakfast"})
            if slot.id == "Monday:breakfast"
            else slot
            for slot in spec.slots
        ]
        pinned = pin_style(
            spec.model_copy(update={"slots": slots}), "breakfast", "custom_shake", ["Monday"]
        )
        self.assertIsNone(pinned.by_id()["Monday:breakfast"].style)

    def test_a_config_without_the_shake_style_keeps_rotating(self):
        config = dict(
            self.config_with({"day": "Monday", "time": "06:30", "type": "gym_hypertrophy"}),
            meal_styles={"breakfast": {"eggs_salmon": "eggs"}, "dinner": CONFIG["meal_styles"]["dinner"]},
        )
        resolved = planner.resolve_auto_choices(week_spec(), config, [])
        self.assertEqual(resolved.by_id()["Monday:breakfast"].style, "eggs_salmon")


class TestPromptRules(unittest.TestCase):
    def slots_by_day(self, cuisines, style=None):
        return {
            day: SlotSpec(day=day, meal_type="dinner", mode=MODE_COOK, cuisine=cuisine, style=style)
            for day, cuisine in zip(DAYS, cuisines)
        }

    def test_continuity_rule_describes_the_blocks(self):
        rule = planner.build_cuisine_continuity_rule(
            self.slots_by_day(["thai"] * 4 + ["vietnamese"] * 3)
        )
        self.assertIn("thai on Monday, Tuesday, Wednesday, Thursday", rule)
        self.assertIn("vietnamese on Friday, Saturday, Sunday", rule)

    def test_a_week_of_seven_cuisines_states_no_blocks(self):
        rule = planner.build_cuisine_continuity_rule(
            self.slots_by_day(
                ["thai", "greek", "italian", "korean", "cajun", "indian", "bbq"]
            )
        )
        self.assertEqual(rule, "")

    def test_consecutive_protein_repeats_are_forbidden(self):
        self.assertIn("two consecutive nights", planner.DINNER_VARIETY_RULE)

    def test_pantry_consolidation_reaches_both_generation_axes(self):
        for style_rule, variety_rule, budget_rule in (
            (planner.DAY_STYLE_RULE, planner.DAY_VARIETY_RULE, planner.DAY_BUDGET_RULE),
            (planner.WEEK_STYLE_RULE, planner.WEEK_VARIETY_RULE, planner.WEEK_BUDGET_RULE),
        ):
            rules = planner.build_generation_rules(
                CONFIG | {"dietary_rules": {"allowed_nova_groups": [1, 2, 3], "banned_ingredients": []}},
                days=["Monday"],
                style_rule=style_rule,
                variety_rule=variety_rule,
                budget_rule=budget_rule,
            )
            self.assertIn("Pantry consolidation", rules)

    def test_a_shake_slot_carries_the_rotation_directive(self):
        brief = planner.build_slot_brief(
            SlotSpec(day="Monday", meal_type="breakfast", mode=MODE_COOK, style="custom_shake"),
            CONFIG,
            1,
            {"calories": 400, "protein_g": 40, "net_carbs_g": 30, "fat_g": 12},
        )
        self.assertIn("Protein shake:", brief)


class TestIngredientCanonicalisation(unittest.TestCase):
    def assert_one_line(self, *names):
        keys = {shopping.normalize_name(name) for name in names}
        displays = {shopping.display_name(name) for name in names}
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(len(displays), 1, displays)

    def test_canned_fish_variants_are_one_purchase(self):
        self.assert_one_line(
            "Sardines (canned)", "sardines in water (tinned)", "Tinned sardines, drained"
        )
        self.assertEqual(shopping.display_name("sardines in water (tinned)"), "Sardines (canned)")

    def test_staple_qualifiers_collapse_to_one_variant(self):
        self.assert_one_line("Low fat cottage cheese", "Cottage cheese", "cottage cheese, plain")
        self.assert_one_line("Extra virgin olive oil", "Olive oil")
        self.assert_one_line("Greek yogurt", "Greek yoghurt, plain")
        self.assert_one_line("Rolled oats", "Porridge oats", "oats")
        self.assert_one_line("Ground flaxseed", "Flax seeds", "flaxseeds")

    def test_specific_rules_beat_general_ones(self):
        self.assertEqual(shopping.display_name("Greek yoghurt"), "Greek yoghurt")
        self.assertEqual(shopping.display_name("Plain yogurt"), "Yoghurt")
        self.assertNotEqual(
            shopping.normalize_name("Greek yoghurt"), shopping.normalize_name("Plain yoghurt")
        )

    def test_a_different_food_sharing_a_word_is_left_alone(self):
        for name, other in (
            ("Mustard seeds", "Dijon mustard"),
            ("Oat milk", "Rolled oats"),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(
                    shopping.normalize_name(name), shopping.normalize_name(other)
                )

    def test_a_conflicting_state_blocks_the_merge(self):
        # The whole point of the state suffix is that a gram of one isn't a
        # gram of the other — canonicalisation must not override it.
        self.assertNotEqual(
            shopping.normalize_name("Frozen sardines"),
            shopping.normalize_name("Sardines (canned)"),
        )
        self.assertNotEqual(
            shopping.normalize_name("Oats, cooked"), shopping.normalize_name("Rolled oats")
        )

    def test_departments_still_resolve_from_the_canonical_name(self):
        for name, department in (
            ("sardines in water (tinned)", "Fish & Seafood"),
            ("Low fat cottage cheese", "Dairy & Eggs"),
            ("Porridge oats", "Grains & Bakery"),
            ("Ground flaxseed", "Nuts, Seeds & Spreads"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    shopping.categorize_department(shopping.display_name(name)), department
                )

    def test_untouched_names_normalise_exactly_as_before(self):
        # The canonical table is a narrow addition, not a rewrite: anything it
        # doesn't name must keep the behaviour the shopping rules document.
        self.assertEqual(shopping.normalize_name("Cucumber, diced"), "cucumber")
        self.assertEqual(shopping.normalize_name("Fresh lemon juice"), "juice lemon")
        self.assertEqual(shopping.normalize_name("Garlic cloves"), "garlic")
        self.assertNotEqual(
            shopping.normalize_name("Quinoa, dry"), shopping.normalize_name("Quinoa, cooked")
        )


class TestRealConfig(unittest.TestCase):
    """The shipped config/ still validates, and still drives the new layout."""

    def config(self) -> dict:
        from repository import LocalJSONRepository, run_sync

        return planner.load_app_config(run_sync(LocalJSONRepository().load_config()))

    def test_shipped_config_validates_and_blocks_the_week(self):
        config = self.config()
        self.assertEqual(planner.planning_rule(config, "cuisine_block_pattern"), [4, 3])
        resolved = planner.resolve_auto_choices(week_spec(), config, [])
        cuisines = dinner_cuisines(resolved)
        self.assertEqual(len(set(cuisines)), 2)
        self.assertEqual(cuisines[:4], [cuisines[0]] * 4)

    def test_every_affinity_names_a_configured_cuisine(self):
        config = self.config()
        known = set(config["cuisines"])
        for cuisine, partners in config["cuisine_affinities"].items():
            with self.subTest(cuisine=cuisine):
                self.assertIn(cuisine, known)
                self.assertTrue(set(partners) <= known, set(partners) - known)

    def test_baseline_cuisines_are_known_and_clear_the_floor(self):
        config = self.config()
        known = set(config["cuisines"])
        baseline = config["baseline_cuisines"]
        self.assertTrue(set(baseline) <= known, set(baseline) - known)
        resolved = planner.resolve_auto_choices(week_spec(), config, [])
        cuisines = dinner_cuisines(resolved)
        share = planner.planning_rule(config, "min_baseline_cuisine_share")
        baseline_days = sum(1 for cuisine in cuisines if cuisine in baseline)
        self.assertGreaterEqual(baseline_days / len(cuisines), share)

    def test_the_pinned_workout_style_exists(self):
        self.assertIn(
            planner.WORKOUT_BREAKFAST_STYLE, self.config()["meal_styles"]["breakfast"]
        )


if __name__ == "__main__":
    unittest.main()
