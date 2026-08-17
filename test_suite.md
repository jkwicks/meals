# Test Suite, Integration Tests & Fixtures

=== File: tests/test_week_composition.py ===
"""Tests for how a week is composed before a single token is generated.

Three deterministic, API-free decisions live between `default_week_spec` and
the first API call, and all three were added to cut food waste rather than to
change what a meal looks like:

- **Cuisine blocks** (`cuisine_block_sizes`, `pick_cuisine_blocks`) — four
  nights of one cuisine and three of a complementary second, instead of seven
  countries that share nothing on the shopping list.
- **Workout-synced breakfasts** (`morning_training_days`, `week.pin_style`) —
  a morning gym or cardio session forces that day's breakfast to a shake.
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

    def test_morning_gym_and_cardio_qualify(self):
        config = self.config_with(
            {"day": "Monday", "time": "06:30", "type": "gym_hypertrophy"},
            {"day": "Thursday", "time": "07:15", "type": "cardio_run"},
        )
        self.assertEqual(planner.morning_training_days(config), ["Monday", "Thursday"])

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

    def test_the_pinned_workout_style_exists(self):
        self.assertIn(
            planner.WORKOUT_BREAKFAST_STYLE, self.config()["meal_styles"]["breakfast"]
        )


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_sync_service.py ===
"""Tests for `src/integrations/sync_service.py`.

`unittest` from the standard library, matching `test_nutrition_engine.py` —
this venv carries no pytest, so `python -m unittest discover tests` runs
everything with no new dependency.

Nothing here touches the network or a real account. The two clients are the
only parts that do, and they are reached through one seam each
(`GarminSyncService.client()`, `CronometerSyncService._rows_*`), so a fake
substituted at that seam exercises every line of mapping below it. The
payload shapes the fakes return are the real ones — grams for Garmin mass,
`Energy (kcal)`-style headers for the Cronometer CSV — because the mapping is
the whole point of the module and a fake speaking the app's own dialect would
test nothing.
"""

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_SRC / "integrations"))

import sync_service as sync  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402


class FakeGarminClient:
    """The subset of `garminconnect.Garmin` the service actually calls.

    Weights are in grams and sleep in seconds, which is what Garmin really
    sends — the unit conversion is one of the things most likely to regress.
    """

    def __init__(self, weight_list=None, activities=None, sleep=None):
        self._weight_list = weight_list
        self._activities = activities if activities is not None else []
        self._sleep = sleep

    def get_body_composition(self, startdate, enddate=None):
        if self._weight_list is None:
            return {"dateWeightList": [], "totalAverage": {}}
        return {"dateWeightList": self._weight_list}

    def get_activities_by_date(self, startdate, enddate, activitytype=None):
        return self._activities

    def get_sleep_data(self, cdate):
        if self._sleep is None:
            raise RuntimeError("no sleep data")
        return self._sleep


def garmin_service(**kwargs):
    service = sync.GarminSyncService(email="user@example.com", password="pw")
    service._client = FakeGarminClient(**kwargs)
    return service


@contextlib.contextmanager
def replace_garmin_client(fake):
    """Make `sync_garmin` build a service wired to `fake`.

    `sync_garmin` constructs its own `GarminSyncService`, which is the point —
    the write guard being tested lives in that function, not in the service —
    so the seam has to be the class rather than an injected instance.
    """
    original = sync.GarminSyncService

    class Patched(original):
        def __init__(self, *args, **kwargs):
            super().__init__(email="user@example.com", password="pw")
            self._client = fake

    sync.GarminSyncService = Patched
    try:
        yield
    finally:
        sync.GarminSyncService = original


FULL_READING = {
    "weight": 98400,
    "bodyFat": 27.5,
    "muscleMass": 38200,
    "bodyWater": 52.1,
    "bmi": 29.4,
}


class TestBodyComposition(unittest.TestCase):
    def test_grams_are_converted_to_kilograms(self):
        """Garmin sends mass in grams; `weigh_ins` is documented in kg.

        Storing 98400 would not look like an error anywhere downstream — it
        would look like a person, and every derived target would be computed
        from it.
        """
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        self.assertEqual(entry["weight_kg"], 98.4)
        self.assertEqual(entry["muscle_mass_kg"], 38.2)

    def test_percentages_pass_through_unscaled(self):
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        self.assertEqual(entry["body_fat_pct"], 27.5)
        self.assertEqual(entry["water_pct"], 52.1)
        self.assertEqual(entry["bmi"], 29.4)

    def test_latest_measurement_wins(self):
        """Two readings in one morning is a normal thing for a smart scale."""
        entry = garmin_service(
            weight_list=[{"weight": 99000}, {"weight": 98400}]
        ).fetch_body_composition("2026-08-16")
        self.assertEqual(entry["weight_kg"], 98.4)

    def test_absent_metrics_are_omitted_not_zeroed(self):
        """A scale reporting only weight must not blank a stored body fat.

        `save_biometric_entry` merges on `date`, so a key present with 0.0
        overwrites while an absent key leaves the earlier reading alone.
        """
        entry = garmin_service(
            weight_list=[{"weight": 98400}]
        ).fetch_body_composition("2026-08-16")
        self.assertEqual(entry["weight_kg"], 98.4)
        for absent in ("body_fat_pct", "muscle_mass_kg", "water_pct", "bmi"):
            self.assertNotIn(absent, entry)

    def test_no_reading_yields_date_only(self):
        """A day the scale wasn't used is a real state, not a zero-weight day."""
        entry = garmin_service(weight_list=None).fetch_body_composition("2026-08-16")
        self.assertEqual(entry, {"date": "2026-08-16"})

    def test_malformed_date_is_rejected(self):
        with self.assertRaises(ValueError):
            garmin_service(weight_list=[FULL_READING]).fetch_body_composition("16/08/2026")


ACTIVITIES = [
    {
        "activityId": 1,
        "activityName": "Elliptical",
        "activityType": {"typeKey": "elliptical"},
        "calories": 640,
        "duration": 3600,
        "averageHR": 132,
    },
    {
        "activityId": 2,
        "activityName": "Morning lift",
        "activityType": {"typeKey": "strength_training"},
        "calories": 300,
        "duration": 2700,
    },
    {
        "activityId": 3,
        "activityName": "Treadmill",
        "activityType": {"typeKey": "treadmill_running"},
        "calories": 500,
        "duration": 1800,
    },
]


class TestCardioActivities(unittest.TestCase):
    def test_only_cardio_modalities_are_returned(self):
        sessions = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )
        self.assertEqual([s["type"] for s in sessions], ["elliptical", "treadmill_running"])

    def test_net_calories_apply_the_recovery_factor(self):
        """The discount is the reason this method exists — see the constant."""
        sessions = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )
        self.assertEqual(sessions[0]["net_calories"], 320)
        self.assertEqual(sessions[1]["net_calories"], 250)

    def test_gross_is_kept_alongside_net(self):
        """A silently adjusted number can't be reconciled against the watch."""
        session = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )[0]
        self.assertEqual(session["gross_calories"], 640)
        self.assertLess(session["net_calories"], session["gross_calories"])

    def test_recovery_factor_is_the_documented_half(self):
        """Pinned: changing it silently re-scales every exercise figure."""
        self.assertEqual(sync.EXERCISE_RECOVERY_FACTOR, 0.50)

    def test_duration_is_reported_in_minutes(self):
        session = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )[0]
        self.assertEqual(session["duration_min"], 60.0)

    def test_missing_calories_do_not_crash(self):
        sessions = garmin_service(
            activities=[{"activityType": {"typeKey": "elliptical"}, "duration": 600}]
        ).fetch_cardio_activities("2026-08-16")
        self.assertEqual(sessions[0]["net_calories"], 0)

    def test_no_activities_is_an_empty_list(self):
        self.assertEqual(
            garmin_service(activities=[]).fetch_cardio_activities("2026-08-16"), []
        )


SLEEP = {
    "dailySleepDTO": {
        "sleepScores": {"overall": {"value": 83}},
        "sleepTimeSeconds": 26400,
    }
}


class TestReadiness(unittest.TestCase):
    def test_sleep_score_is_reported(self):
        readiness = garmin_service(sleep=SLEEP).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["sleep_score"], 83.0)
        self.assertEqual(readiness["readiness"], "excellent")
        self.assertEqual(readiness["sleep_hours"], 7.33)

    def test_readiness_carries_no_energy_figure(self):
        """The rule this method exists to enforce.

        A sleep score is a unitless index; anything here that looked like
        kcal would be an invitation to add it to a day's energy, and there is
        no conversion that would make that legitimate.
        """
        readiness = garmin_service(sleep=SLEEP).fetch_readiness("2026-08-16")
        for key in readiness:
            self.assertNotIn("calor", key.lower())
            self.assertNotIn("energy", key.lower())
            self.assertNotIn("kcal", key.lower())

    def test_bands_match_garmins_own(self):
        self.assertEqual(sync._readiness_label(80), "excellent")
        self.assertEqual(sync._readiness_label(79), "good")
        self.assertEqual(sync._readiness_label(60), "good")
        self.assertEqual(sync._readiness_label(59), "fair")
        self.assertEqual(sync._readiness_label(40), "fair")
        self.assertEqual(sync._readiness_label(39), "poor")
        self.assertIsNone(sync._readiness_label(None))

    def test_sleep_failure_does_not_propagate(self):
        """Supplementary data must not fail a weigh-in sync that worked."""
        readiness = garmin_service(sleep=None).fetch_readiness("2026-08-16")
        self.assertIsNone(readiness["sleep_score"])
        self.assertIsNone(readiness["readiness"])


class TestCronometerMapping(unittest.TestCase):
    """`_daily_summary_row` — the CSV-to-`daily_actuals` fold.

    Split out of `fetch_daily_summary` precisely so it can be tested without
    an account, a paid tier, or a subprocess.
    """

    ROWS = [
        {
            "Date": "2026-08-15",
            "Energy (kcal)": "1820.4",
            "Protein (g)": "141.2",
            "Net Carbs (g)": "88.0",
            "Fat (g)": "71.5",
        },
        {
            "Date": "2026-08-16",
            "Energy (kcal)": "2010",
            "Protein (g)": "150",
            "Net Carbs (g)": "95",
            "Fat (g)": "80",
        },
    ]

    def test_keys_are_the_repositorys_not_cronometers(self):
        """`nutrition_engine` indexes `protein_g`, not `protein`.

        A row keyed the CSV's way stores and displays perfectly and feeds
        nothing — the adaptive loop would simply never adapt, weeks later.
        """
        entry = sync._daily_summary_row(self.ROWS, "2026-08-16")
        self.assertEqual(
            sorted(k for k in entry if k not in ("date", "source")),
            ["calories", "fat_g", "net_carbs_g", "protein_g"],
        )

    def test_the_matching_day_is_selected(self):
        entry = sync._daily_summary_row(self.ROWS, "2026-08-15")
        self.assertEqual(entry["calories"], 1820.4)
        self.assertEqual(entry["protein_g"], 141.2)

    def test_day_column_variant_is_accepted(self):
        """The servings export names it `Day`; the summary has used both."""
        entry = sync._daily_summary_row(
            [{"Day": "2026-08-16", "Energy (kcal)": "2010"}], "2026-08-16"
        )
        self.assertEqual(entry["calories"], 2010.0)

    def test_nothing_logged_yields_date_only(self):
        self.assertEqual(sync._daily_summary_row([], "2026-08-16"), {"date": "2026-08-16"})

    def test_a_lone_undated_row_is_taken_as_the_day(self):
        entry = sync._daily_summary_row([{"Energy (kcal)": "1500"}], "2026-08-16")
        self.assertEqual(entry["calories"], 1500.0)

    def test_ambiguous_rows_are_not_guessed(self):
        """Guessing here overwrites a good day's entry with another day's."""
        entry = sync._daily_summary_row(
            [{"Energy (kcal)": "1"}, {"Energy (kcal)": "2"}], "2026-08-16"
        )
        self.assertEqual(entry, {"date": "2026-08-16"})


class TestCoercion(unittest.TestCase):
    def test_as_float_rejects_non_numbers(self):
        for junk in ("--", "", None, True, "n/a"):
            self.assertIsNone(sync._as_float(junk))

    def test_as_float_accepts_numeric_strings(self):
        self.assertEqual(sync._as_float(" 3.5 "), 3.5)

    def test_prune_keeps_date_but_drops_nones(self):
        self.assertEqual(
            sync._prune({"date": "2026-08-16", "a": None, "b": 1}),
            {"date": "2026-08-16", "b": 1},
        )

    def test_prune_keeps_a_dateless_none_date(self):
        self.assertIn("date", sync._prune({"date": None}))

    def test_iso_rejects_non_iso(self):
        for bad in ("16-08-2026", "August 16", "", None):
            with self.assertRaises(ValueError):
                sync._iso(bad)

    def test_cardio_matching_is_substring_based(self):
        """Garmin sub-types freely; exact matching would drop real sessions."""
        self.assertTrue(sync._is_cardio("treadmill_running"))
        self.assertTrue(sync._is_cardio("indoor_cycling"))
        self.assertTrue(sync._is_cardio("virtual_ride"))
        self.assertFalse(sync._is_cardio("strength_training"))
        self.assertFalse(sync._is_cardio("walking"))


class TestPersistence(unittest.TestCase):
    """The round trip through a real `LocalJSONRepository` on a temp file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self):
        with open(self.path) as handle:
            return json.load(handle)

    def test_weigh_in_lands_in_the_weigh_ins_list(self):
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        run_sync(self.repo.save_biometric_entry(entry))
        self.assertEqual(self.stored()["weigh_ins"][0]["weight_kg"], 98.4)

    def test_actuals_land_in_the_daily_actuals_list(self):
        entry = sync._daily_summary_row(TestCronometerMapping.ROWS, "2026-08-16")
        run_sync(self.repo.save_daily_actuals(entry))
        row = self.stored()["daily_actuals"][0]
        self.assertEqual(row["protein_g"], 150.0)

    def test_resync_updates_rather_than_duplicates(self):
        service = garmin_service(weight_list=[FULL_READING])
        run_sync(self.repo.save_biometric_entry(service.fetch_body_composition("2026-08-16")))
        run_sync(self.repo.save_biometric_entry(service.fetch_body_composition("2026-08-16")))
        self.assertEqual(len(self.stored()["weigh_ins"]), 1)

    def test_sync_garmin_stores_nothing_when_the_scale_was_not_used(self):
        """Regression: `source` alone once passed for a measurement.

        `sync_garmin` decided by `len(entry) > 2`, which the provenance tag
        cleared on its own — so a day the scale never saw was written as a
        weigh-in with no weight, and `get_latest_biometrics` would hand that
        empty row back as the most recent reading.

        Driven through `sync_garmin` rather than the guard directly, because
        the guard was never the broken part: the call site was.
        """
        with replace_garmin_client(FakeGarminClient(weight_list=None)):
            result = sync.sync_garmin("2026-08-16", self.repo)

        self.assertEqual(result["weigh_in"], {"date": "2026-08-16"})
        self.assertEqual(run_sync(self.repo.load_biometrics())["weigh_ins"], [])

    def test_sync_garmin_stores_a_real_reading(self):
        """The other half of the guard: a real weigh-in must still land."""
        with replace_garmin_client(FakeGarminClient(weight_list=[FULL_READING])):
            sync.sync_garmin("2026-08-16", self.repo)

        weigh_ins = run_sync(self.repo.load_biometrics())["weigh_ins"]
        self.assertEqual(len(weigh_ins), 1)
        self.assertEqual(weigh_ins[0]["weight_kg"], 98.4)
        self.assertEqual(weigh_ins[0]["source"], "garmin")

    def test_partial_resync_preserves_earlier_metrics(self):
        """The pay-off of omitting absent keys rather than zeroing them."""
        full = garmin_service(weight_list=[FULL_READING])
        run_sync(self.repo.save_biometric_entry(full.fetch_body_composition("2026-08-16")))

        partial = garmin_service(weight_list=[{"weight": 98100}])
        run_sync(self.repo.save_biometric_entry(partial.fetch_body_composition("2026-08-16")))

        row = self.stored()["weigh_ins"][0]
        self.assertEqual(row["weight_kg"], 98.1)
        self.assertEqual(row["body_fat_pct"], 27.5)


class TestCronometerCredentialGuards(unittest.TestCase):
    def test_missing_credentials_fail_before_any_call(self):
        service = sync.CronometerSyncService(username="", password="")
        with self.assertRaises(RuntimeError) as caught:
            service.fetch_daily_summary("2026-08-16")
        self.assertIn("CRONOMETER_USERNAME", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_planner_dynamic_targets.py ===
"""Tests for the biometric-driven half of `src/planner.py`.

Covers the three pieces that turn a measured body into a prompt:
`hydrate_dynamic_targets` (the day's macros come from the scale, not the file),
`apply_protein_floor` (the day's locked protein reaches every meal), and
`logged_intake_for` (today's Cronometer row beats the plan).

All three are pure functions of their arguments — no repository, no event loop,
no API — which is the point of `hydrate_config` being the only async wrapper
around them. `unittest` and the `sys.path` insert match
`test_nutrition_engine.py`; see its docstring for why.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402  (path setup must precede the import)
from week import MODE_COOK, MODE_LEFTOVER, SlotSpec  # noqa: E402

# config/profile.json's own user_profile. Every expected number below is worked
# out by hand from it rather than captured from a run, so a changed constant
# fails loudly instead of re-baselining itself against the code under test.
PROFILE = {
    "birth_date": "1971-01-10",
    "height_cm": 183,
    "gender": "male",
    "target_weight_kg": 80.0,
    "protein_multiplier": 1.8,
    "activity_level": "light_office",
}
# Katch-McArdle on 98.4 kg at 27.5% body fat: LBM 71.34, BMR 1910.9,
# TDEE x1.375 = 2627.5, deficit 718.0 at 18.4 kg to go, so 1910 kcal.
WEIGH_IN = {"date": "2026-08-16", "weight_kg": 98.4, "body_fat_pct": 27.5}
DYNAMIC_KCAL = 1910
LOCKED_PROTEIN_G = 144.0


def config_with(**overrides) -> dict:
    """A minimal config carrying the two sections hydration reads."""
    base = {
        "user_profile": dict(PROFILE),
        "weekly_schedule": {
            "Monday": {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55,
                       "meal_overrides": {"breakfast": {"calories": 400}}},
            "Wednesday": {"calories": 1000, "protein_g": 110, "net_carbs_g": 60, "fat_g": 35,
                          "meal_overrides": {}},
        },
    }
    base.update(overrides)
    return base


class TestHydrateDynamicTargets(unittest.TestCase):
    def setUp(self):
        self.hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN)

    def test_calories_come_from_the_scale_not_the_file(self):
        for day in ("Monday", "Wednesday"):
            self.assertEqual(self.hydrated["weekly_schedule"][day]["calories"], DYNAMIC_KCAL)

    def test_protein_is_locked_to_the_target_weight_every_day(self):
        """80 kg x 1.8, identical on both days, where the file said 120 and 110.

        The floor must not track the scale — that would shrink it exactly as
        the diet started to threaten the lean mass it exists to protect.
        """
        for day in ("Monday", "Wednesday"):
            self.assertEqual(
                self.hydrated["weekly_schedule"][day]["protein_g"], LOCKED_PROTEIN_G
            )

    def test_carb_cycling_survives(self):
        """Each day's own net_carbs_g is passed *into* the engine, not replaced."""
        self.assertEqual(self.hydrated["weekly_schedule"]["Monday"]["net_carbs_g"], 130.0)
        self.assertEqual(self.hydrated["weekly_schedule"]["Wednesday"]["net_carbs_g"], 60.0)

    def test_fat_absorbs_the_difference_between_the_two_days(self):
        """Same energy and protein, different carbs, so only fat moves."""
        monday = self.hydrated["weekly_schedule"]["Monday"]
        wednesday = self.hydrated["weekly_schedule"]["Wednesday"]
        # 70 g of carbs at 4 kcal is 280 kcal, which is 31.1 g of fat at 9.
        # `delta` rather than `places` because each figure is independently
        # rounded to 1 dp before the subtraction sees it.
        self.assertAlmostEqual(wednesday["fat_g"] - monday["fat_g"], 280 / 9, delta=0.1)

    def test_meal_overrides_are_untouched(self):
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["meal_overrides"],
            {"breakfast": {"calories": 400}},
        )

    def test_the_basis_is_recorded_for_diagnosis(self):
        basis = self.hydrated["dynamic_basis"]
        self.assertEqual(basis["bmr_method"], "katch_mcardle")
        self.assertEqual(basis["current_weight_kg"], 98.4)


class TestHydrationFallsBack(unittest.TestCase):
    def test_no_weight_anywhere_keeps_the_files_targets(self):
        """The engine raises; the planner must not.

        `weekly_schedule` holds real targets somebody chose, so falling back
        plans a configured week rather than a fabricated body. biometrics.json
        is empty until the first Garmin sync, so this is the normal path on a
        fresh checkout.
        """
        config = config_with()
        with self.assertLogs("meals", level="WARNING"):
            self.assertIs(planner.hydrate_dynamic_targets(config, None), config)

    def test_the_fallback_is_announced(self):
        """One note, not one per day: every day fails identically, because they
        differ only in the carb figure the failure never reaches."""
        notes = []
        with self.assertLogs("meals", level="WARNING"):
            planner.hydrate_dynamic_targets(config_with(), None, notes.append)
        self.assertEqual(len(notes), 1)
        self.assertIn("config.json targets", notes[0])

    def test_an_unfilled_profile_is_left_alone(self):
        config = config_with(user_profile={"protein_multiplier": 1.8, "activity_level": "light_office"})
        self.assertIs(planner.hydrate_dynamic_targets(config, WEIGH_IN), config)


class TestTrainingUpliftSurvivesHydration(unittest.TestCase):
    """A workout expands the day; hydration must not quietly undo that."""

    def setUp(self):
        config = config_with(
            meal_types=["breakfast", "lunch", "dinner", "snack"],
            meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            training_schedule=[{
                "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                "estimated_burn_kcal": 350,
            }],
        )
        self.adjusted = planner.apply_training_adjustments(config)
        self.hydrated = planner.hydrate_dynamic_targets(self.adjusted, WEIGH_IN)

    def test_the_uplift_is_recorded(self):
        self.assertEqual(self.adjusted["training_uplift"]["Monday"]["calories"], 350.0)

    def test_the_burn_is_replayed_onto_the_dynamic_baseline(self):
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL + 350
        )

    def test_protein_stays_locked_on_a_training_day(self):
        """`TRAINING_INTENSITY_SPLIT` would add 43.75 g; the lock wins.

        The workout's energy buys back carbs and fat, not protein — 'locked'
        has to mean locked or the 144 g figure is only a resting-day number.
        """
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["protein_g"], LOCKED_PROTEIN_G
        )

    def test_the_carb_share_is_not_double_counted(self):
        """apply_training_adjustments already put it in weekly_schedule, and
        hydration passes that figure through the engine untouched."""
        # 130 from the file + 43.75 from the burn's carb share, at hydration's
        # 1 dp. Double-counting would read 217.5.
        self.assertEqual(self.hydrated["weekly_schedule"]["Monday"]["net_carbs_g"], 173.8)


def cook_slot(meal_type: str, mode: str = MODE_COOK) -> SlotSpec:
    return SlotSpec(
        id=planner.slot_id("Monday", meal_type), day="Monday", meal_type=meal_type, mode=mode
    )


class TestProteinFloor(unittest.TestCase):
    """`min_meal_protein_g` moves grams between meals; it never creates any."""

    def setUp(self):
        self.slots = [cook_slot(m) for m in ("lunch", "dinner", "snack")]
        # A weight-only split of 114 g across 0.30/0.30/0.10.
        self.budgets = {
            "Monday:lunch": {"calories": 668.6, "protein_g": 48.857, "net_carbs_g": 15.0, "fat_g": 47.0},
            "Monday:dinner": {"calories": 668.6, "protein_g": 48.857, "net_carbs_g": 15.0, "fat_g": 47.0},
            "Monday:snack": {"calories": 222.9, "protein_g": 16.286, "net_carbs_g": 5.0, "fat_g": 15.7},
        }
        self.before = {k: dict(v) for k, v in self.budgets.items()}
        self.after = planner.apply_protein_floor(
            self.budgets, self.slots, {slot.id: 1 for slot in self.slots}, 35.0
        )

    def test_every_meal_reaches_the_floor(self):
        for slot in self.slots:
            self.assertGreaterEqual(self.after[slot.id]["protein_g"], 35.0)

    def test_the_days_protein_is_conserved(self):
        self.assertAlmostEqual(
            sum(b["protein_g"] for b in self.before.values()),
            sum(b["protein_g"] for b in self.after.values()),
            places=6,
        )

    def test_calories_move_with_the_protein(self):
        """4 kcal per gram transferred, so each budget still reconciles and the
        day's calorie total is conserved as exactly as its protein."""
        for slot in self.slots:
            moved = self.after[slot.id]["protein_g"] - self.before[slot.id]["protein_g"]
            self.assertAlmostEqual(
                self.after[slot.id]["calories"],
                self.before[slot.id]["calories"] + moved * 4,
                places=6,
            )
        self.assertAlmostEqual(
            sum(b["calories"] for b in self.before.values()),
            sum(b["calories"] for b in self.after.values()),
            places=6,
        )

    def test_donors_give_in_proportion_to_their_surplus(self):
        """Lunch and dinner are identical, so they must give identically."""
        self.assertAlmostEqual(
            self.after["Monday:lunch"]["protein_g"],
            self.after["Monday:dinner"]["protein_g"],
            places=6,
        )

    def test_an_unaffordable_floor_changes_nothing(self):
        """Raising some meals and starving others would be an arbitrary choice
        about which meal gets short-changed; a day that can't carry n x 35 g is
        a target problem, not a split problem."""
        budgets = {slot.id: {"calories": 200.0, "protein_g": 12.0,
                             "net_carbs_g": 10.0, "fat_g": 8.0} for slot in self.slots}
        with self.assertLogs("meals", level="WARNING") as logged:
            after = planner.apply_protein_floor(
                budgets, self.slots, {slot.id: 1 for slot in self.slots}, 35.0
            )
        self.assertEqual([b["protein_g"] for b in after.values()], [12.0, 12.0, 12.0])
        # Left visible rather than silently absorbed, same as the
        # overspent-override branch in `split_targets`.
        self.assertIn("protein floor", logged.output[0])

    def test_a_single_slot_is_left_alone(self):
        """It already holds the whole flexible remainder — there is nowhere to
        move grams from."""
        budgets = {"Monday:dinner": {"calories": 400.0, "protein_g": 20.0,
                                     "net_carbs_g": 10.0, "fat_g": 20.0}}
        after = planner.apply_protein_floor(budgets, [cook_slot("dinner")], {}, 35.0)
        self.assertEqual(after["Monday:dinner"]["protein_g"], 20.0)

    def test_a_batch_meal_is_weighed_by_what_it_costs_the_day(self):
        """A lunch eaten twice spends twice its budget, so its shortfall against
        the floor is twice as expensive to fix."""
        slots = [cook_slot("lunch"), cook_slot("dinner")]
        budgets = {
            "Monday:lunch": {"calories": 400.0, "protein_g": 30.0, "net_carbs_g": 10.0, "fat_g": 20.0},
            "Monday:dinner": {"calories": 800.0, "protein_g": 60.0, "net_carbs_g": 20.0, "fat_g": 40.0},
        }
        after = planner.apply_protein_floor(
            budgets, slots, {"Monday:lunch": 2, "Monday:dinner": 1}, 35.0
        )
        self.assertAlmostEqual(after["Monday:lunch"]["protein_g"], 35.0, places=6)
        # 5 g short x 2 servings = 10 g off dinner, which had 25 g to spare.
        self.assertAlmostEqual(after["Monday:dinner"]["protein_g"], 50.0, places=6)


class TestSplitTargetsAppliesTheFloor(unittest.TestCase):
    """The floor is reached through `split_targets`, not called directly."""

    def setUp(self):
        self.config = {
            "meal_weights": {"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            "planning_rules": dict(planner.DEFAULT_PLANNING_RULES),
        }
        self.slots = [cook_slot(m) for m in ("breakfast", "lunch", "dinner", "snack")]
        self.remaining = {"calories": 1910.0, "protein_g": 144.0,
                          "net_carbs_g": 60.0, "fat_g": 121.6}

    def test_the_snack_is_lifted_off_its_weighted_share(self):
        budgets = planner.split_targets(
            self.remaining, self.slots, {}, self.config, {}
        )
        # 0.10 of 144 g is 14.4 — a snack with no protein source in it.
        self.assertGreaterEqual(budgets["Monday:snack"]["protein_g"], 35.0)

    def test_a_leftover_slot_is_not_floored(self):
        """Its protein comes from the recipe its source cooked, not a budget."""
        slots = [cook_slot("lunch"), cook_slot("dinner"), cook_slot("snack", MODE_LEFTOVER)]
        budgets = planner.split_targets(self.remaining, slots, {}, self.config, {})
        self.assertLess(budgets["Monday:snack"]["protein_g"], 35.0)

    def test_a_pinned_meal_keeps_its_verbatim_budget(self):
        overrides = {"breakfast": {"calories": 350.0, "protein_g": 30.0,
                                   "net_carbs_g": 25.0, "fat_g": 12.0}}
        budgets = planner.split_targets(
            self.remaining, self.slots, {}, self.config, overrides
        )
        self.assertEqual(budgets["Monday:breakfast"]["protein_g"], 30.0)
        self.assertGreaterEqual(budgets["Monday:snack"]["protein_g"], 35.0)


class TestLoggedIntakeFor(unittest.TestCase):
    """Today's Cronometer row, or None — never a guess."""

    # A Sunday, so the weekday check has something to match and mismatch.
    NOW = datetime(2026, 8, 16, 17, 0)
    ROW = {"date": "2026-08-16", "calories": 1120, "protein_g": 98,
           "net_carbs_g": 40, "fat_g": 45}

    def test_todays_row_is_returned(self):
        logged = planner.logged_intake_for("Sunday", {"daily_actuals": [self.ROW]}, self.NOW)
        self.assertEqual(logged["calories"], 1120.0)
        self.assertEqual(logged["protein_g"], 98.0)

    def test_another_weekday_is_not_today(self):
        """A SlotSpec carries only a weekday name, so a Thursday being planned
        ahead is not the Thursday that was logged."""
        self.assertIsNone(
            planner.logged_intake_for("Thursday", {"daily_actuals": [self.ROW]}, self.NOW)
        )

    def test_a_stale_row_is_ignored(self):
        stale = dict(self.ROW, date="2026-08-09")
        self.assertIsNone(
            planner.logged_intake_for("Sunday", {"daily_actuals": [stale]}, self.NOW)
        )

    def test_an_all_zero_row_reads_as_no_data(self):
        """A dated shell from a partial sync must not be read as 'you have
        eaten nothing today', which would hand one meal the whole day."""
        shell = {"date": "2026-08-16", "calories": 0}
        self.assertIsNone(
            planner.logged_intake_for("Sunday", {"daily_actuals": [shell]}, self.NOW)
        )

    def test_missing_macros_are_zero_not_a_dropped_row(self):
        partial = {"date": "2026-08-16", "calories": 900, "protein_g": 70}
        logged = planner.logged_intake_for("Sunday", {"daily_actuals": [partial]}, self.NOW)
        self.assertEqual(logged["calories"], 900.0)
        self.assertEqual(logged["fat_g"], 0.0)

    def test_no_biometrics_at_all(self):
        self.assertIsNone(planner.logged_intake_for("Sunday", None, self.NOW))
        self.assertIsNone(planner.logged_intake_for("Sunday", {}, self.NOW))


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_nutrition_engine.py ===
"""Tests for `src/nutrition_engine.py`.

Written against `unittest` from the standard library rather than pytest, which
this venv doesn't carry — `python -m unittest discover tests` runs them with no
new dependency. They are plain `TestCase` classes, so they also run untouched
under `python -m pytest` if pytest is ever added to requirements.txt.

`src/` is not a package (see CLAUDE.md), so the import below puts it on
`sys.path` the same way `python src/planner.py` does.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nutrition_engine as ne  # noqa: E402  (path setup must precede the import)


# The profile the app actually ships in config/profile.json, at the top of the
# journey: 100 kg heading for 80 kg. Every expected number below is worked out
# by hand from these, so a change to a constant fails loudly rather than
# quietly re-baselining itself against the code it is meant to check.
PROFILE = {
    "birth_date": "1971-01-10",
    "height_cm": 183,
    "gender": "male",
    "target_weight_kg": 80.0,
    "protein_multiplier": 1.8,
    "activity_level": "light_office",
}


class TestAgeFromBirthDate(unittest.TestCase):
    def test_age_on_reference_date(self):
        # Any date on/after the Jan-10 birthday in 2026 gives age 55; picked
        # arbitrarily rather than reusing the real calendar date, since
        # `on_date` is passed explicitly and the test asserts nothing about
        # when it's run.
        reference_date = date(2026, 1, 15)
        self.assertEqual(ne.age_from_birth_date("1971-01-10", reference_date), 55)

    def test_birthday_not_yet_reached_this_year(self):
        """The day before a birthday must still be the younger age."""
        self.assertEqual(ne.age_from_birth_date("1971-01-10", date(2026, 1, 9)), 54)
        self.assertEqual(ne.age_from_birth_date("1971-01-10", date(2026, 1, 10)), 55)

    def test_malformed_date_raises(self):
        with self.assertRaises(ValueError):
            ne.age_from_birth_date("10/01/1971")


class TestBMR(unittest.TestCase):
    def test_mifflin_st_jeor_male(self):
        # 10*100 + 6.25*183 - 5*55 + 5 = 1000 + 1143.75 - 275 + 5
        self.assertAlmostEqual(
            ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="male"),
            1873.75,
        )

    def test_mifflin_st_jeor_female_differs_by_the_sex_constant(self):
        male = ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="male")
        female = ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="female")
        self.assertAlmostEqual(male - female, 166.0)  # +5 vs -161

    def test_katch_mcardle_used_when_body_fat_known(self):
        # LBM = 100 * (1 - 0.25) = 75kg; 370 + 21.6*75 = 370 + 1620
        self.assertAlmostEqual(
            ne.calculate_bmr(
                weight_kg=100, height_cm=183, age=55, body_fat_pct=25.0
            ),
            1990.0,
        )

    def test_katch_ignores_height_age_and_gender(self):
        """Not incidental — lean mass already encodes what they proxy for."""
        baseline = ne.calculate_bmr(
            weight_kg=100, height_cm=183, age=55, gender="male", body_fat_pct=25.0
        )
        self.assertAlmostEqual(
            ne.calculate_bmr(
                weight_kg=100, height_cm=150, age=20, gender="female", body_fat_pct=25.0
            ),
            baseline,
        )

    def test_impossible_body_fat_rejected_rather_than_used(self):
        """A scale reporting 0% would otherwise read as pure lean mass and
        inflate BMR by hundreds of kcal."""
        for bad in (0.0, 2.9, 70.1, 95.0):
            with self.assertRaises(ValueError):
                ne.calculate_bmr(
                    weight_kg=100, height_cm=183, age=55, body_fat_pct=bad
                )

    def test_missing_height_raises_on_the_mifflin_path(self):
        with self.assertRaises(ValueError):
            ne.calculate_bmr(weight_kg=100, height_cm=None, age=55)

    def test_unknown_gender_raises(self):
        with self.assertRaises(ValueError):
            ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="mail")


class TestTDEE(unittest.TestCase):
    def test_activity_factors(self):
        for level, factor in (
            ("sedentary", 1.2),
            ("light_office", 1.375),
            ("moderate", 1.55),
        ):
            self.assertAlmostEqual(ne.calculate_tdee(2000, level), 2000 * factor)

    def test_default_is_light_office(self):
        self.assertAlmostEqual(ne.calculate_tdee(2000), 2750.0)

    def test_unknown_level_raises_rather_than_defaulting(self):
        """A typo'd activity level must not silently cost ~350 kcal/day."""
        with self.assertRaises(ValueError):
            ne.calculate_tdee(2000, "lightly_active")


class TestDynamicDeficit(unittest.TestCase):
    def test_full_deficit_at_and_above_the_ceiling_weight(self):
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(100.0, 80.0), 750.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(120.0, 80.0), 750.0)

    def test_floor_deficit_at_and_below_target(self):
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(80.0, 80.0), 350.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(74.0, 80.0), 350.0)

    def test_midpoint_is_halfway_between_the_anchors(self):
        # 90kg is halfway from 80 -> 100, so halfway from 350 -> 750.
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(90.0, 80.0), 550.0)

    def test_deficit_falls_monotonically_as_weight_falls(self):
        weights = [100.0, 97.5, 95.0, 92.5, 90.0, 87.5, 85.0, 82.5, 80.0]
        deficits = [ne.calculate_dynamic_deficit(w, 80.0) for w in weights]
        self.assertEqual(deficits, sorted(deficits, reverse=True))
        self.assertEqual(deficits[0], 750.0)
        self.assertEqual(deficits[-1], 350.0)

    def test_target_above_the_ceiling_weight_does_not_divide_by_zero(self):
        """A 105kg target leaves no ramp between target and the 100kg anchor."""
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(110.0, 105.0), 750.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(100.0, 105.0), 350.0)


class TestDeriveFatG(unittest.TestCase):
    def test_fat_spends_the_remaining_energy(self):
        # 2000 - (150*4 + 60*4) = 2000 - 840 = 1160; /9
        self.assertAlmostEqual(ne.derive_fat_g(2000, 150, 60), 1160 / 9)

    def test_macros_reconcile_back_to_the_calorie_figure(self):
        fat = ne.derive_fat_g(1826, 144, 60)
        self.assertAlmostEqual(144 * 4 + 60 * 4 + fat * 9, 1826)

    def test_floored_at_zero_when_protein_and_carbs_overspend(self):
        self.assertEqual(ne.derive_fat_g(500, 150, 60), 0.0)


class _FixedToday(date):
    """`date` with `.today()` pinned.

    `calculate_macro_targets` resolves age via `age_from_birth_date(birth_date)`
    with no reference date, so it falls back to the real `date.today()`. Left
    unpatched, every expected number below that assumes age 55 (BMR, TDEE,
    ...) is only correct for real-world dates in the 55th year after the
    profile's 1971-01-10 birth date — this class quietly starts failing the
    day that year ends, with no code change to explain why. Subclassing
    (rather than replacing `date` outright) keeps every other use of `date` in
    `nutrition_engine` — the `date(...)` constructor, `_parse_iso_date` — working
    unchanged.
    """

    @classmethod
    def today(cls):
        return cls(2026, 8, 16)


class TestMacroTargets(unittest.TestCase):
    """The headline case: 55 years old, 100 kg, aiming at 80 kg."""

    def setUp(self):
        patcher = mock.patch("nutrition_engine.date", _FixedToday)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.result = ne.calculate_macro_targets(
            PROFILE, {"date": "2026-08-16", "weight_kg": 100.0}
        )

    def test_protein_is_locked_to_target_weight(self):
        # 80 * 1.8 -- the number the whole protein-floor idea rests on.
        self.assertAlmostEqual(self.result["protein_g"], 144.0)

    def test_protein_does_not_move_when_current_weight_does(self):
        """The floor must not sink as the scale does."""
        for weight in (100.0, 95.0, 88.0, 81.0):
            result = ne.calculate_macro_targets(PROFILE, {"weight_kg": weight})
            self.assertAlmostEqual(result["protein_g"], 144.0)

    def test_calories_are_tdee_minus_the_dynamic_deficit(self):
        # BMR 1873.75 -> TDEE *1.375 = 2576.40625 -> less 750 at 100kg
        self.assertAlmostEqual(self.result["basis"]["bmr"], 1873.8, places=1)
        self.assertAlmostEqual(self.result["basis"]["tdee"], 2576.4, places=1)
        self.assertAlmostEqual(self.result["basis"]["deficit_kcal"], 750.0)
        self.assertEqual(self.result["calories"], round(2576.40625 - 750))

    def test_carbs_default_to_the_baseline(self):
        self.assertAlmostEqual(self.result["net_carbs_g"], ne.DEFAULT_NET_CARBS_G)

    def test_schedule_can_override_carbs(self):
        loaded = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0}, net_carbs_g=130.0
        )
        self.assertAlmostEqual(loaded["net_carbs_g"], 130.0)

    def test_the_four_macros_reconcile(self):
        macros = {key: self.result[key] for key in ne.MACRO_KEYS}
        spent = (
            macros["protein_g"] * 4 + macros["net_carbs_g"] * 4 + macros["fat_g"] * 9
        )
        # Within a kcal, the rounding applied to the reported grams.
        self.assertAlmostEqual(spent, macros["calories"], delta=1.0)

    def test_result_carries_exactly_the_macro_keys_plus_basis(self):
        """`DaySchedule` is extra='forbid', so diagnostics must stay nested."""
        self.assertEqual(set(self.result), set(ne.MACRO_KEYS) | {"basis"})

    def test_body_fat_reading_switches_to_katch_mcardle(self):
        result = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0, "body_fat_pct": 25.0}
        )
        self.assertEqual(result["basis"]["bmr_method"], "katch_mcardle")
        self.assertAlmostEqual(result["basis"]["bmr"], 1990.0)

    def test_zero_body_fat_reading_falls_back_to_mifflin(self):
        """Scales write 0 rather than omitting the key when they can't read."""
        result = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0, "body_fat_pct": 0}
        )
        self.assertEqual(result["basis"]["bmr_method"], "mifflin_st_jeor")

    def test_deficit_shrinks_as_the_target_nears(self):
        far = ne.calculate_macro_targets(PROFILE, {"weight_kg": 100.0})
        near = ne.calculate_macro_targets(PROFILE, {"weight_kg": 82.0})
        self.assertGreater(
            far["basis"]["deficit_kcal"], near["basis"]["deficit_kcal"]
        )
        self.assertAlmostEqual(near["basis"]["deficit_kcal"], 390.0)

    def test_missing_weight_raises_rather_than_inventing_a_body(self):
        with self.assertRaises(ValueError):
            ne.calculate_macro_targets(PROFILE, None)

    def test_missing_age_and_body_fat_raises(self):
        profile = dict(PROFILE)
        del profile["birth_date"]
        with self.assertRaises(ValueError):
            ne.calculate_macro_targets(profile, {"weight_kg": 100.0})

    def test_body_fat_covers_for_a_profile_with_no_birth_date(self):
        """Katch needs neither age nor height, so this must not raise."""
        profile = dict(PROFILE)
        del profile["birth_date"]
        del profile["height_cm"]
        result = ne.calculate_macro_targets(
            profile, {"weight_kg": 100.0, "body_fat_pct": 25.0}
        )
        self.assertAlmostEqual(result["basis"]["bmr"], 1990.0)


def _series(start_weight, kg_lost, days, calories, noise=None):
    """A weigh-in/log pair describing a steady `kg_lost` over `days`."""
    first = date(2026, 8, 1)
    noise = noise or [0.0] * (days + 1)
    weigh_ins = [
        {
            "date": str(first + timedelta(days=i)),
            "weight_kg": start_weight - kg_lost * (i / days) + noise[i],
        }
        for i in range(days + 1)
    ]
    logs = [
        {"date": str(first + timedelta(days=i)), "calories": calories}
        for i in range(days + 1)
    ]
    return logs, weigh_ins


class TestAdaptiveTDEE(unittest.TestCase):
    def test_recovers_the_true_expenditure_from_a_clean_series(self):
        """2000 kcal/day while losing 1kg/14 days => 2000 + 7700/14."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2550.0, places=1
        )

    def test_is_unbiased_where_smoothed_endpoints_were_not(self):
        """Guards the regression fix: reading the trend off an exponentially
        smoothed series' endpoints returned 2405.7 for this exact input, a
        144 kcal/day understatement on noise-free data."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        estimate = ne.calculate_adaptive_tdee(logs, weigh_ins)
        self.assertGreater(estimate, 2500.0)

    def test_gaining_weight_reads_as_expenditure_below_intake(self):
        """Sign check — inverting the delta doubles the error, so this is the
        test that catches it."""
        logs, weigh_ins = _series(100.0, -1.0, 14, 3000)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 3000 - 7700 / 14, places=1
        )

    def test_maintenance_reads_back_as_the_logged_intake(self):
        logs, weigh_ins = _series(100.0, 0.0, 14, 2400)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2400.0, places=1
        )

    def test_none_when_there_are_too_few_weigh_ins(self):
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, weigh_ins[:1]))
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, []))

    def test_none_when_the_span_is_too_short_to_read(self):
        """A 3-day span multiplied by 7700 is noise amplification."""
        logs, weigh_ins = _series(100.0, 0.3, 3, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, weigh_ins))

    def test_none_when_no_calories_were_logged(self):
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee([], weigh_ins))
        undated = [{"weight_kg": 99.0} for _ in range(5)]
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, undated))

    def test_window_excludes_older_weigh_ins(self):
        """A 60-day series read with window_days=14 must use only the last
        fortnight, so an old, faster stretch cannot inflate the estimate."""
        logs, weigh_ins = _series(110.0, 10.0, 56, 2000)
        narrow = ne.calculate_adaptive_tdee(logs, weigh_ins, window_days=14)
        wide = ne.calculate_adaptive_tdee(logs, weigh_ins, window_days=56)
        # Same underlying rate here, so both land together -- what is being
        # checked is that the narrow window still produces an estimate at all.
        self.assertIsNotNone(narrow)
        self.assertAlmostEqual(narrow, wide, delta=1.0)

    def test_irregular_weighing_gives_the_same_rate_as_daily(self):
        """The regression runs on real elapsed days, so skipping days must not
        change the answer."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        sparse = [weigh_ins[i] for i in (0, 3, 4, 9, 13, 14)]
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, sparse),
            ne.calculate_adaptive_tdee(logs, weigh_ins),
            places=1,
        )

    def test_malformed_rows_are_skipped_not_fatal(self):
        """biometrics.json is hand-editable; one bad row must cost that row."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        weigh_ins.insert(3, {"date": "not-a-date", "weight_kg": 40.0})
        weigh_ins.insert(6, {"weight_kg": 40.0})
        logs.insert(2, {"date": "2026-08-03", "calories": None})
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2550.0, places=1
        )

    def test_scale_noise_is_damped_relative_to_endpoint_differencing(self):
        """The worst case for a fitted trend: both noise spikes land on the
        endpoints, where leverage is highest, and push the same way — a
        dehydrated first morning and a heavy last one. Differencing those two
        weigh-ins sees the whole 1kg loss cancel and reports maintenance, a
        550 kcal/day error. The fit uses all 15 points, so the same input
        costs it ~193.

        Asserted as a ratio against the naive estimator rather than as a fixed
        tolerance, because what the design claims is *relative*: fitting the
        series beats differencing its ends. A bare delta would either encode
        today's arithmetic as a magic number or pass for the wrong reason.
        """
        noise = [0.0] * 15
        noise[0] = -0.5   # dehydrated on the first morning
        noise[14] = 0.5   # heavy on the last
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000, noise=noise)

        true_tdee = 2000 + 7700 / 14
        naive = 2000 + (
            weigh_ins[0]["weight_kg"] - weigh_ins[-1]["weight_kg"]
        ) * 7700 / 14
        estimate = ne.calculate_adaptive_tdee(logs, weigh_ins)

        self.assertAlmostEqual(naive, 2000.0, places=1)  # the trend, erased
        self.assertLess(abs(estimate - true_tdee), abs(naive - true_tdee) / 2)


class TestSmoothSeries(unittest.TestCase):
    def test_smoothing_reduces_variation_but_keeps_the_level(self):
        raw = [100.0, 101.0, 99.0, 100.5, 99.5, 100.0]
        smoothed = ne.smooth_series(raw)
        self.assertEqual(len(smoothed), len(raw))
        spread = max(smoothed) - min(smoothed)
        self.assertLess(spread, max(raw) - min(raw))
        self.assertAlmostEqual(sum(smoothed) / len(smoothed), 100.0, delta=0.5)

    def test_empty_series(self):
        self.assertEqual(ne.smooth_series([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
-e 

=== File: tests/fixtures/config_snapshot.json ===
{
  "base_schedule": {
    "Friday": "Office",
    "Monday": "Office",
    "Saturday": "Outing",
    "Sunday": "Home",
    "Thursday": "Office",
    "Tuesday": "WFH",
    "Wednesday": "WFH"
  },
  "cuisine_affinities": {
    "bbq": [
      "cajun",
      "mexican"
    ],
    "cajun": [
      "bbq",
      "mexican"
    ],
    "greek": [
      "mediterranean",
      "middle_eastern"
    ],
    "indian": [
      "middle_eastern",
      "thai"
    ],
    "italian": [
      "mediterranean",
      "greek"
    ],
    "japanese": [
      "korean",
      "vietnamese"
    ],
    "korean": [
      "japanese",
      "bbq"
    ],
    "mediterranean": [
      "italian",
      "greek"
    ],
    "mexican": [
      "cajun",
      "bbq"
    ],
    "middle_eastern": [
      "greek",
      "mediterranean"
    ],
    "thai": [
      "vietnamese",
      "indian"
    ],
    "vietnamese": [
      "thai",
      "japanese"
    ]
  },
  "cuisine_meal_types": [
    "dinner"
  ],
  "cuisines": [
    "mexican",
    "indian",
    "thai",
    "japanese",
    "korean",
    "bbq",
    "middle_eastern",
    "italian",
    "mediterranean",
    "vietnamese",
    "cajun",
    "greek"
  ],
  "diet_styles": {
    "aip": {
      "label": "Autoimmune Protocol (AIP)",
      "principles": "Exclude grains, legumes, nightshades (tomato, pepper, eggplant, potato), dairy, eggs and seed-based spices; build meals from organ meats, clean proteins, bone broth and non-nightshade vegetables."
    },
    "anti_inflammatory": {
      "label": "Anti-Inflammatory",
      "principles": "Load meals with polyphenol-rich vegetables, cruciferous greens, wild fatty fish and therapeutic spices and aromatics (turmeric, ginger, garlic); avoid refined seed oils and refined carbohydrates."
    },
    "blue_zones": {
      "label": "Blue Zones / Longevity",
      "principles": "Build the plate almost entirely from whole plants \u2014 legumes, beans, nuts, seeds, wild herbs and extra virgin olive oil \u2014 with wild-caught fish only 2-3 times a week and meat rare to never."
    },
    "dash": {
      "label": "DASH Diet",
      "principles": "Favor vegetables, fruit, low-fat dairy and whole grains; minimize added salt and sodium-heavy ingredients such as cured meats, brined or canned-in-salt items, and salty condiments."
    },
    "fast_800": {
      "label": "Fast 800",
      "principles": "Keep dishes simple and calorie-light within the given budget: lean protein and vegetables first, minimal added fat, sauce or oil, and no refined carbs padding the plate."
    },
    "low_fodmap": {
      "label": "Low-FODMAP",
      "principles": "Avoid high-FODMAP ingredients \u2014 onion, garlic, wheat, most legumes, high-fructose fruit \u2014 substituting garlic-infused oil, scallion or spring-onion greens, fresh ginger and chives, and favor gut-friendly produce like zucchini, bok choy and carrots."
    },
    "mediterranean_diet": {
      "label": "Mediterranean Diet",
      "principles": "Favor olive oil, oily fish, legumes, whole grains and vegetables; limit red meat and butter to occasional use."
    },
    "mind_diet": {
      "label": "MIND Diet",
      "principles": "Include leafy greens daily and berries at least twice a week; favor walnuts, olive oil, poultry and fish; strictly limit cheese, butter, fried food and pastries."
    },
    "nordic_diet": {
      "label": "Nordic Diet",
      "principles": "Favor fatty cold-water fish (mackerel, salmon, herring), root vegetables, cruciferous vegetables and berries, dressed in cold-pressed rapeseed or flaxseed oil rather than tropical oils or butter."
    },
    "paleo": {
      "label": "Paleo",
      "principles": "Build meals from pastured meat, seafood, eggs, fibrous vegetables, root vegetables, fruit and nuts; exclude all grains, legumes, refined sugar and standard dairy."
    },
    "pegan": {
      "label": "Pegan",
      "principles": "Give non-starchy, low-glycemic vegetables and healthy fats (avocado, nuts, seeds, olive oil) roughly three-quarters of the plate, with clean animal protein as a smaller side rather than the centerpiece."
    },
    "total_wellbeing_diet": {
      "label": "Total Wellbeing Diet",
      "principles": "High protein with moderate low-GI carbs, lean meat or dairy at most meals, and strict portion discipline \u2014 no incidental extras beyond what the budget calls for."
    }
  },
  "dietary_rules": {
    "active_diet_styles": [],
    "allowed_nova_groups": [
      1,
      2,
      3
    ],
    "banned_ingredients": [
      "high fructose corn syrup",
      "artificial sweeteners",
      "hydrogenated oil",
      "MSG",
      "seed oils",
      "soy protein isolate",
      "eggplant",
      "apricot",
      "banana",
      "Almond milk",
      "soy beans"
    ]
  },
  "enable_sunday_prep": true,
  "inventory_rules": {
    "fridge_safe_days": 4,
    "perishable_day_gap": 3
  },
  "inventory_to_clear": [],
  "location_rules": {
    "Office": {
      "lunch_mode": "leftover",
      "max_prep_minutes": 0,
      "restrictions": [
        "portable",
        "no_reheat",
        "no_long_prep"
      ]
    },
    "Outing": {
      "dinner_mode": "leftover",
      "lunch_mode": "skip",
      "notes": "Dining out or packed nutrition"
    },
    "WFH": {
      "lunch_mode": "cook",
      "max_prep_minutes": 15,
      "restrictions": [
        "quick_cook"
      ]
    }
  },
  "max_prep_active_mins": 120,
  "meal_styles": {
    "breakfast": {
      "beans_toast": "Classic baked beans served on toasted whole-grain bread.",
      "custom_shake": "Protein Shake Template. BASE (REQUIRED): 1.5 scoops protein powder, 5g creatine, 300ml water. DYNAMIC INGREDIENT SELECTION (choose 2-4 items to meet target): Fruits (100g frozen mixed berries, frozen mango, frozen cherries), Carbs (15g Barley max, 15g Oats), Protein Boost (80g yoghurt, cottage cheese, milk, silken tofu), Nuts (20g almonds, 20g walnuts), Seeds (1 tsp flaxseeds, 1 tsp chia seeds, 1 tsp hemp seeds), Greens/Vege (30g spinach, 50g frozen raw broccoli, 50g frozen raw cauliflower), Flavor/Spice (cocoa powder, ginger, mustard seeds, turmeric).",
      "eggs_salmon": "Scrambled eggs and smoked salmon served on toasted whole-grain bread.",
      "fish_pate": "Tinned sardines or tinned mackerel pate served on toasted whole-grain bread.",
      "yoghurt_bowl": "Yoghurt Bowl. BASE: 120g yoghurt. SEEDS: 1 tsp flaxseeds, 1 tsp chia seeds, 1 tsp hemp seeds, pumpkin seeds. FRUIT: 20g fresh blueberries, 20g fresh strawberries. NUTS: 20g almonds, 20g walnuts."
    },
    "dinner": {
      "braise": "a slow-braised pot dish that improves after resting",
      "curry": "a simmered curry or stew in sauce, served with a side",
      "grill": "grilled or pan-seared protein with cooked vegetable sides",
      "one_pan": "a single tray-bake or one-pan meal",
      "roast": "a roasted joint or whole bird with roasted vegetables",
      "stir_fry": "a fast high-heat stir fry"
    },
    "lunch": {
      "cold_plate": "a no-cook cold plate: cured/cooked protein, cheese, raw veg, dips",
      "grain_bowl": "a warm grain or legume bowl with protein and vegetables",
      "salad": "a substantial cold salad with a protein on top",
      "soup": "a hearty soup or broth, reheatable",
      "wrap": "a wrap, flatbread or hand-held roll"
    },
    "snack": {
      "boiled_eggs": "boiled eggs with seasoning",
      "fruit_and_protein": "fruit paired with a protein source",
      "nuts_seeds": "a portion of nuts and seeds",
      "yoghurt": "yoghurt with a simple topping"
    }
  },
  "meal_types": [
    "breakfast",
    "lunch",
    "dinner",
    "snack"
  ],
  "meal_weights": {
    "breakfast": 0.3,
    "dinner": 0.3,
    "lunch": 0.3,
    "snack": 0.1
  },
  "planning_rules": {
    "cuisine_block_pattern": [
      4,
      3
    ],
    "history_max_entries": 21,
    "min_meal_protein_g": 35.0,
    "portion_trim_deadband": 0.03,
    "portion_trim_limits": [
      0.6,
      1.6
    ],
    "protein_avoid_window": 6,
    "protein_lookback_entries": 3
  },
  "regional": {
    "country": "AU",
    "hemisphere": "southern",
    "postcode": "3350",
    "state": "VIC"
  },
  "serving_rules": {
    "servings_per_meal": 2
  },
  "shopping": {
    "shop_days": [
      "Sunday",
      "Wednesday"
    ]
  },
  "training_schedule": [
    {
      "day": "Monday",
      "duration_minutes": 60,
      "estimated_burn_kcal": 350,
      "time": "18:00",
      "type": "gym_hypertrophy"
    }
  ],
  "ui_settings": {
    "bar_scale_limit": 1.6,
    "title_tooltip_chars": 38
  },
  "user_profile": {
    "activity_level": "light_office",
    "birth_date": "1971-01-10",
    "gender": "male",
    "height_cm": 183.0,
    "protein_multiplier": 1.8,
    "target_weight_kg": 80.0
  },
  "week_defaults": {
    "breakfast": "cook",
    "dinner": "cook",
    "lunch": "cook",
    "snack": "cook"
  },
  "week_start_day": "Monday",
  "weekly_schedule": {
    "Friday": {
      "calories": 1000.0,
      "fat_g": 35.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 350,
          "fat_g": 12,
          "net_carbs_g": 25,
          "protein_g": 30
        }
      },
      "net_carbs_g": 60.0,
      "protein_g": 110.0
    },
    "Monday": {
      "calories": 1500.0,
      "fat_g": 55.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 400,
          "fat_g": 15,
          "net_carbs_g": 35,
          "protein_g": 30
        }
      },
      "net_carbs_g": 130.0,
      "protein_g": 120.0
    },
    "Saturday": {
      "calories": 1200.0,
      "fat_g": 44.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 400,
          "fat_g": 15,
          "net_carbs_g": 35,
          "protein_g": 30
        }
      },
      "net_carbs_g": 85.0,
      "protein_g": 115.0
    },
    "Sunday": {
      "calories": 1500.0,
      "fat_g": 55.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 400,
          "fat_g": 15,
          "net_carbs_g": 35,
          "protein_g": 30
        }
      },
      "net_carbs_g": 130.0,
      "protein_g": 120.0
    },
    "Thursday": {
      "calories": 1000.0,
      "fat_g": 35.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 350,
          "fat_g": 12,
          "net_carbs_g": 25,
          "protein_g": 30
        }
      },
      "net_carbs_g": 60.0,
      "protein_g": 110.0
    },
    "Tuesday": {
      "calories": 1200.0,
      "fat_g": 44.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 400,
          "fat_g": 15,
          "net_carbs_g": 35,
          "protein_g": 30
        }
      },
      "net_carbs_g": 85.0,
      "protein_g": 115.0
    },
    "Wednesday": {
      "calories": 1000.0,
      "fat_g": 35.0,
      "meal_overrides": {
        "breakfast": {
          "calories": 350,
          "fat_g": 12,
          "net_carbs_g": 25,
          "protein_g": 30
        }
      },
      "net_carbs_g": 60.0,
      "protein_g": 110.0
    }
  }
}
-e 

=== File: tests/test_config_layout.py ===
"""The safety net for the config/reference/data/logs split.

The refactor moves `data/config.json` into six files under `config/` and has
`LocalJSONRepository.load_config()` merge them back into the one flat dict
`AppConfig` has always validated. Every consumer downstream — `planner`,
`week`, `ui_app` — keeps reading `config["weekly_schedule"]`,
`config["meal_styles"]` and the rest, so the whole refactor is correct exactly
when that merged dict is unchanged.

`fixtures/config_snapshot.json` is that dict, captured before the first file
moved. A key that lands in the wrong file, gets dropped by the merge, or
changes type on the way through fails here — one assertion naming the key —
rather than surfacing weeks later as a week generated against a silently
defaulted value.

Regenerate the fixture only when a config value is *deliberately* changed, and
in the same commit as the change, so the diff shows exactly which keys moved:

    source venv/bin/activate && python tests/test_config_layout.py --update

Additions are allowed and removals are not: `config/schedule.json` brings the
`base_schedule`/`location_rules`/`regional` keys in with it, declared on
`AppConfig` before anything reads them, and a new key can't break a caller
that doesn't know it exists. A *missing* key always can.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "config_snapshot.json"


def merged_config() -> dict:
    """The shipped config as every caller downstream sees it.

    Deliberately `load_config()` and not `load_config_with_models()`:
    models.json is reshaped by this refactor on purpose (`meal_generation_model`,
    per-model metadata), so pinning its contents here would turn an intended
    change into a test failure. What must not change is config.json's own
    merged output.
    """
    return planner.load_app_config(run_sync(LocalJSONRepository().load_config()))


class TestMergedConfigIsUnchanged(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = json.loads(SNAPSHOT.read_text())
        self.config = merged_config()

    def test_no_key_was_lost(self):
        missing = sorted(set(self.snapshot) - set(self.config))
        self.assertEqual(
            missing,
            [],
            f"config keys vanished in the split: {missing}. Either the key "
            f"landed in no file, or its file is absent from the merge manifest.",
        )

    def test_every_value_survived_intact(self):
        # Per-key subTest rather than one dict comparison: a 200-line
        # weekly_schedule diff buries which key actually moved, and the whole
        # point of this file is to name it.
        for key, expected in sorted(self.snapshot.items()):
            with self.subTest(key=key):
                self.assertEqual(self.config.get(key), expected)

    def test_snapshot_is_not_empty(self):
        # Guards the failure mode where a regeneration runs against a broken
        # or half-written config and quietly bakes in an empty baseline,
        # after which every other test here passes vacuously.
        self.assertGreaterEqual(len(self.snapshot), 20)
        self.assertIn("weekly_schedule", self.snapshot)


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(merged_config(), indent=2, sort_keys=True) + "\n")
        print(f"Wrote {SNAPSHOT.relative_to(Path.cwd())}")
    else:
        unittest.main()
-e 

=== File: tests/test_history.py ===
"""Tests for dating meal_history.json entries and archiving their planned
targets (week.day_date, planner.record_week_history) — Phase 5a of the UI
roadmap in CLAUDE.md.

Real `LocalJSONRepository` on a temp directory, `run_sync` to bridge the
async call from a plain `unittest.TestCase` — same pattern
`test_sync_service.TestPersistence` already uses for a repository round trip.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from planner import CookEvent, Ingredient, Recipe, WeekPlan, record_week_history  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402
from week import day_date  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

TARGETS = {
    day: {"calories": 2200.0, "protein_g": 144.0, "net_carbs_g": 180.0, "fat_g": 70.0}
    for day in DAYS
}


def recipe(name: str, meal_type: str) -> Recipe:
    return Recipe(
        name=name,
        meal_type=meal_type,
        ingredients=[
            Ingredient(
                name="Chicken thigh",
                quantity_g=200,
                nova_group=1,
                calories=400,
                protein_g=40,
                net_carbs_g=0,
                fat_g=25,
            )
        ],
        instructions=["Cook it."],
        prep_time_minutes=20,
    )


def week_plan(**overrides) -> WeekPlan:
    defaults = dict(
        days=DAYS,
        servings_per_meal=2,
        generated_at="2026-08-16T20:31:21",
        week_start_date="2026-08-10",
        cook_events=[
            CookEvent(
                slot_id="Sunday:dinner",
                day="Sunday",
                meal_type="dinner",
                portions=2,
                style="grill",
                cuisine="middle_eastern",
                recipe=recipe("Lamb Sirloin", "dinner"),
            ),
        ],
        slots=[],
        targets=TARGETS,
    )
    defaults.update(overrides)
    return WeekPlan(**defaults)


class TestDayDate(unittest.TestCase):
    def test_first_day_is_the_start_date_itself(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Monday"), "2026-08-10")

    def test_last_day_is_six_days_later(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Sunday"), "2026-08-16")

    def test_a_middle_day_is_its_own_offset(self):
        self.assertEqual(day_date("2026-08-10", DAYS, "Wednesday"), "2026-08-12")

    def test_a_week_start_day_other_than_monday_still_offsets_by_rotation_position(self):
        rotated = ["Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Monday", "Tuesday"]
        self.assertEqual(day_date("2026-08-12", rotated, "Monday"), "2026-08-17")


class TestRecordWeekHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def entry_for(self, day: str) -> dict:
        history = run_sync(self.repo.load_history())
        return next(e for e in history if e["day_of_week"] == day)

    def test_a_cooked_day_is_dated_from_week_start_date(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        self.assertEqual(self.entry_for("Sunday")["date"], "2026-08-16")

    def test_the_days_planned_targets_are_archived_verbatim(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        self.assertEqual(self.entry_for("Sunday")["targets"], TARGETS["Sunday"])

    def test_a_plan_with_no_week_start_date_records_no_date(self):
        """Pre-Phase-4 plans (or a bare `regenerate_single_day` on one)
        carry no week_start_date — recording a guessed date for a *past*
        day would be worse than recording none, unlike today_in_week's
        live fallback for "does this cover today"."""
        run_sync(record_week_history(week_plan(week_start_date=None), self.repo, config=None))
        self.assertIsNone(self.entry_for("Sunday")["date"])

    def test_a_day_missing_from_targets_records_no_targets_rather_than_raising(self):
        sparse_targets = {k: v for k, v in TARGETS.items() if k != "Sunday"}
        run_sync(record_week_history(week_plan(targets=sparse_targets), self.repo, config=None))
        self.assertIsNone(self.entry_for("Sunday")["targets"])

    def test_existing_fields_are_unaffected_by_the_new_ones(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        entry = self.entry_for("Sunday")
        self.assertEqual(entry["cuisine"], "middle_eastern")
        self.assertEqual(entry["styles"], {"dinner": "grill"})
        self.assertEqual(entry["recipe_names"], ["Lamb Sirloin"])

    def test_a_day_with_no_cook_events_is_not_recorded_at_all(self):
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        history = run_sync(self.repo.load_history())
        recorded_days = {e["day_of_week"] for e in history}
        self.assertEqual(recorded_days, {"Sunday"})

    def test_old_untouched_entries_keep_validating_with_no_date_or_targets_key(self):
        """Regression guard: entries written before this change have neither
        key at all (not even `None`) — history.json is a plain list of
        dicts, never Pydantic-validated (repository.py deals in plain
        dicts/lists), so nothing should choke on their absence."""
        pre_migration_entry = {
            "day_of_week": "Saturday",
            "generated_at": "2026-07-01T09:00:00",
            "cuisine": "thai",
            "styles": {},
            "main_proteins": [],
            "recipe_names": ["Old Dish"],
        }
        run_sync(self.repo.save_history([pre_migration_entry]))
        run_sync(record_week_history(week_plan(), self.repo, config=None))
        history = run_sync(self.repo.load_history())
        self.assertEqual(history[0]["day_of_week"], "Saturday")
        self.assertNotIn("date", history[0])
        self.assertEqual(history[1]["date"], "2026-08-16")


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_diet_styles.py ===
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
-e 

