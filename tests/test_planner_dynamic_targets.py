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


def biometrics_series(daily_kcal: float, kg_lost_over_window: float = 0.7) -> dict:
    """14 days of weigh-ins and logs, losing `kg_lost_over_window` in total.

    A linear decline rather than a realistic wobble: `calculate_adaptive_tdee`
    fits a least-squares slope, and a clean line makes the expected figure
    something a reader can check by hand — mean intake plus (kg/day x 7700).
    """
    days = [f"2026-08-{day:02d}" for day in range(3, 17)]
    step = kg_lost_over_window / (len(days) - 1)
    return {
        "weigh_ins": [
            {"date": day, "weight_kg": round(98.4 - step * i, 3)}
            for i, day in enumerate(days)
        ],
        "daily_actuals": [
            {"date": day, "calories": daily_kcal, "protein_g": 150,
             "net_carbs_g": 120, "fat_g": 80}
            for day in days
        ],
    }


class TestAdaptiveTdeeReachesTheTargets(unittest.TestCase):
    """The loop closing: measured intake and weight trend correcting TDEE.

    Before this, `calculate_adaptive_tdee` was fully built and tested and
    called by nothing — `daily_actuals` was written to disk by the Cronometer
    sync, read once by `logged_intake_for` for a single regenerated meal, and
    never influenced a target. The formula estimate was used even where a
    direct measurement of this body was available.
    """

    def test_without_a_series_the_formula_still_wins(self):
        """Every pre-existing caller omits `biometrics`, and must be unchanged."""
        hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN)
        self.assertEqual(hydrated["dynamic_basis"]["tdee_source"], "formula")
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_a_believable_measurement_replaces_the_formula(self):
        # ~2500 kcal/day eaten while losing 0.7 kg over 13 days is roughly
        # 2500 + (0.0538 x 7700) = ~2915, within 25% of the formula's 2627.
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(2500)
        )
        basis = hydrated["dynamic_basis"]
        self.assertEqual(basis["tdee_source"], "adaptive")
        self.assertGreater(basis["tdee"], basis["tdee_formula"])
        self.assertNotEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_an_implausible_measurement_is_rejected_not_blended(self):
        """Systematic under-logging is the common failure and it depresses the
        estimate — 900 kcal/day "eaten" would otherwise cut the target."""
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(600)
        )
        basis = hydrated["dynamic_basis"]
        self.assertEqual(basis["tdee_source"], "formula_adaptive_rejected")
        self.assertEqual(basis["tdee"], basis["tdee_formula"])
        # Rejected means the week plans exactly as it would have with no data.
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_too_short_a_series_reads_as_no_measurement(self):
        """Two weigh-ins a day apart x 7700 is noise amplification, not data."""
        short = {
            "weigh_ins": [
                {"date": "2026-08-15", "weight_kg": 98.4},
                {"date": "2026-08-16", "weight_kg": 98.1},
            ],
            "daily_actuals": [{"date": "2026-08-16", "calories": 2000}],
        }
        hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN, None, short)
        self.assertEqual(hydrated["dynamic_basis"]["tdee_source"], "formula")

    def test_the_basis_reports_both_numbers(self):
        """Diagnostic, not planning input: two runs a fortnight apart will
        disagree, and `basis` is what says why."""
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(2500)
        )
        basis = hydrated["dynamic_basis"]
        self.assertIsNotNone(basis["tdee_adaptive"])
        self.assertIsNotNone(basis["tdee_formula"])
        self.assertEqual(basis["tdee"], basis["tdee_adaptive"])

    def test_protein_stays_locked_whichever_tdee_wins(self):
        """Energy is what a measurement buys back. Protein is tied to the
        *target* weight and must not move with it."""
        for label, series in [("adaptive", biometrics_series(2500)), ("none", None)]:
            with self.subTest(source=label):
                hydrated = planner.hydrate_dynamic_targets(
                    config_with(), WEIGH_IN, None, series
                )
                for day in hydrated["weekly_schedule"].values():
                    self.assertEqual(day["protein_g"], LOCKED_PROTEIN_G)


class TestEmptyScheduleIsNotAFailure(unittest.TestCase):
    def test_an_empty_weekly_schedule_returns_the_config_untouched(self):
        """The loop never runs, so nothing raises into the fallback — but
        `basis` stays None and there is no day to read a protein figure off,
        which the summary log line used to dereference."""
        config = config_with(weekly_schedule={})
        self.assertIs(planner.hydrate_dynamic_targets(config, WEIGH_IN), config)


if __name__ == "__main__":
    unittest.main()
