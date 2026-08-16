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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nutrition_engine as ne  # noqa: E402  (path setup must precede the import)


# The profile the app actually ships in data/config.json, at the top of the
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
AGE_55 = date(2026, 8, 16)


class TestAgeFromBirthDate(unittest.TestCase):
    def test_age_on_reference_date(self):
        self.assertEqual(ne.age_from_birth_date("1971-01-10", AGE_55), 55)

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


class TestMacroTargets(unittest.TestCase):
    """The headline case: 55 years old, 100 kg, aiming at 80 kg."""

    def setUp(self):
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
